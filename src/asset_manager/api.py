"""AssetManager（§14）：AssetRequest[] → AssetResolutionResult。

链路::

    exact_reference → resolve → inspect → register → bind
    semantic_search → compile → policy/providers → normalize → dedup
                  → inspect → hard filter → rank → select
                  → download/import → registry → bind

模块四不修改 Plan（§73），需要 Module 2 音频分析时输出
``dependency_requests`` 由 Controller 调度（§47/§74）。
"""

from __future__ import annotations

import os
import shutil
from typing import Any, Dict, List, Optional, Tuple

from editing_planner.models import AssetBinding, AssetRequest, ConstraintLevel
from gesture_intent.models import model_dump, model_validate

from .bindings.manager import BindingManager
from .cache.manager import CacheManager, search_cache_key
from .candidates.dedup import dedup_candidates
from .candidates.filter import hard_filter
from .candidates.inspect import Inspector
from .candidates.normalize import normalize_batch
from .candidates.rank import rank_candidates
from .models import (
    AssetCandidate,
    AssetDependencyRequest,
    AssetRecord,
    AssetResolutionResult,
    AssetSource,
    BindingRecord,
    LicenseMetadata,
    ProviderQuery,
    ProviderReport,
    ProviderStatus,
    RegistryScope,
    RejectedCandidate,
    RequestResolution,
    RequestStatus,
    ResolutionMode,
    ResolutionStatus,
    SearchRecord,
    SemanticMetadata,
    SemanticQuery,
    TechnicalMetadata,
)
from .providers.base import AssetProvider, ProviderResult, SearchContext
from .providers.local import LocalLibraryProvider
from .providers.online import OnlineAssetProvider, adapters_from_env
from .providers.user import UserAssetProvider
from .query.compiler import QueryCompiler, split_technical
from .registry import usage as usage_index_ops
from .registry.registry import AssetRegistry
from .registry.store import ProjectStore
from .resolution import policy as policy_mod
from .resolution.resolver import ExactResolver
from .resolution.router import resolution_mode

_MAX_IMPORT_RETRIES = 3  # 在线素材下载复核失败时沿 ranked 顺序重试
_MUSIC_TYPES = {"music", "sound_effect"}


class AssetManager:
    """模块四入口。默认装配 user + local + online 三家 Provider。"""

    def __init__(
        self,
        workspace_root: str = "workspace",
        library_root: str = "data/asset_library",
        project_id: str = "default",
        providers: Optional[Dict[AssetSource, AssetProvider]] = None,
        online_adapters: Optional[List[Any]] = None,
        query_rewriter: Optional[Any] = None,
        inspector: Optional[Inspector] = None,
        rank_weights: Optional[Dict[str, Dict[str, float]]] = None,
        offline: bool = False,
        candidate_limit: int = 10,
    ):
        self.store = ProjectStore(workspace_root, project_id)
        self.state = self.store.load_state()
        self.registry = AssetRegistry(self.state.registry)
        self.binding_mgr = BindingManager(self.state.bindings)
        self.cache = CacheManager(self.store.cache_dir)
        self.inspector = inspector or Inspector()
        self.compiler = QueryCompiler(query_rewriter)
        self.offline = offline
        self.candidate_limit = candidate_limit
        self.rank_weights = rank_weights or {}
        if providers is None:
            providers = {
                AssetSource.user: UserAssetProvider(self.registry.user_records),
                AssetSource.local: LocalLibraryProvider(library_root),
                AssetSource.online: OnlineAssetProvider(
                    online_adapters if online_adapters is not None else adapters_from_env()),
            }
        self.providers = providers
        self._search_seq = 0
        self._dep_seq = 0
        self._deps: List[AssetDependencyRequest] = []
        self._search_pool: Dict[str, SearchRecord] = {}  # request_uid → 最近一次搜索

    # ------------------------------------------------------------------
    # 顶层入口
    # ------------------------------------------------------------------

    def resolve_assets(self, requests: List[Any]) -> AssetResolutionResult:
        """批量消费 AssetRequest（§73）。音乐请求只由 Module 3 显式下发（§6/§83）。"""

        parsed: List[AssetRequest] = [
            model_validate(AssetRequest, r) if isinstance(r, dict) else r
            for r in requests
        ]
        self._deps = []
        result = AssetResolutionResult()
        for request in parsed:
            mode = resolution_mode(request)
            if mode == ResolutionMode.exact_reference:
                resolution, reports, search = self._resolve_exact(request)
            else:
                resolution, reports, search = self._resolve_semantic(request)
            if search is not None:
                result.search_records.append(search)
                self._search_pool[request.request_uid] = search
            result.resolutions.append(resolution)
            result.provider_warnings.extend(
                r for r in reports if r.status != ProviderStatus.success)
            if resolution.binding is not None:
                record_obj = self.registry.get(resolution.binding.asset_uid)
                result.binding_records.append(resolution.binding)
                result.bindings.append(
                    self.binding_mgr.to_planner(resolution.binding, record_obj))
            else:
                result.unresolved_requests.append(request.request_uid)

        result.dependency_requests.extend(self._deps)
        result.status = self._top_status(result, parsed)
        self._persist(result)
        return result

    # ------------------------------------------------------------------
    # exact_reference（§8.1 / §82）
    # ------------------------------------------------------------------

    def _resolve_exact(
        self, request: AssetRequest
    ) -> Tuple[RequestResolution, List[ProviderReport], Optional[SearchRecord]]:
        ctx = self._context(request)
        source_ref = request.source_ref or str(
            request.usage_context.get("source_ref", ""))
        lookup_providers = [
            p for src, p in self.providers.items()
            if src != AssetSource.online and p.capability().supports_exact_lookup
        ]
        candidate, provider_id, misses = ExactResolver().resolve(
            source_ref, lookup_providers, ctx)
        reports = [
            ProviderReport(provider_id=pid, status=ProviderStatus.no_result,
                           message="reference miss")
            for pid in misses
        ]
        if provider_id:
            reports.append(ProviderReport(
                provider_id=provider_id, status=ProviderStatus.success,
                candidate_count=1))
        if candidate is None:
            return RequestResolution(
                request_uid=request.request_uid,
                request_version=request.version,
                status=RequestStatus.unresolved,
                reason=f"reference_not_found:{source_ref}",
            ), reports, None
        record, error = self._import_candidate(candidate, request)
        if record is None:
            return RequestResolution(
                request_uid=request.request_uid,
                request_version=request.version,
                status=RequestStatus.unresolved,
                reason=error or "inspect_failed",
            ), reports, None
        binding = self.binding_mgr.create(
            request, record, score=1.0, breakdown={"exact": 1.0}, alternatives=[])
        self._touch_usage(request, record.asset_uid)
        return RequestResolution(
            request_uid=request.request_uid,
            request_version=request.version,
            status=RequestStatus.resolved,
            binding=binding,
        ), reports, None

    # ------------------------------------------------------------------
    # semantic_search（§8.2 / §14）
    # ------------------------------------------------------------------

    def _resolve_semantic(
        self, request: AssetRequest
    ) -> Tuple[RequestResolution, List[ProviderReport], Optional[SearchRecord]]:

        policy, policy_warning = policy_mod.normalize_policy(request, self.offline)
        if policy is None:
            return RequestResolution(
                request_uid=request.request_uid,
                request_version=request.version,
                status=RequestStatus.unresolved,
                reason=policy_warning or "policy_unsupported",
            ), [], None
        order = policy_mod.provider_order(policy)
        providers, reports = policy_mod.select_providers(
            self.providers, order, request)
        if not providers:
            return RequestResolution(
                request_uid=request.request_uid,
                request_version=request.version,
                status=RequestStatus.unresolved,
                reason="no_capable_provider",
                warnings=[policy_warning] if policy_warning else [],
            ), reports, None

        strategy = policy_mod.search_strategy(request)
        search = self._new_search(request)
        search.provider_ids = [p.provider_id for p in providers]
        ctx = self._context(request)
        semantic_query = self.compiler.parse(request)
        warnings: List[str] = [policy_warning] if policy_warning else []

        collected: List[AssetCandidate] = []
        if strategy == "first_satisfactory":
            # §13.1/§80：逐 Provider 检索，首个产出可过 Hard Filter 的候选即停
            for provider in providers:
                query = self.compiler.compile(
                    request, provider.provider_id, provider.source_kind)
                search.queries[provider.provider_id] = model_dump(query)
                batch, result = self._provider_search(provider, query, ctx)
                reports.append(self._report(provider, result))
                prepped = self._prepare_batch(batch, request, search)
                collected.extend(prepped)
                if prepped:
                    break
        else:
            # §13.2/§81：所有允许 Provider 召回后统一标准化排序
            merged: List[AssetCandidate] = []
            for provider in providers:
                query = self.compiler.compile(
                    request, provider.provider_id, provider.source_kind)
                search.queries[provider.provider_id] = model_dump(query)
                batch, result = self._provider_search(provider, query, ctx)
                reports.append(self._report(provider, result))
                merged.extend(batch)
            collected = self._prepare_batch(merged, request, search)

        if not collected:
            search.status = "empty"
            return RequestResolution(
                request_uid=request.request_uid,
                request_version=request.version,
                status=RequestStatus.unresolved,
                reason="no_candidate_survived",
                warnings=warnings,
                search_uid=search.search_uid,
            ), reports, search

        ranked = rank_candidates(
            collected, request, semantic_query,
            weights=self.rank_weights.get(request.asset_type))
        search.ranked_candidates = ranked
        dep = self._maybe_audio_dependency(request, collected)
        if dep is not None:
            self._deps.append(dep)
            warnings.append(f"dependency_requested:{dep.request_uid}")

        binding = self._select_and_bind(request, collected, ranked, search, warnings)
        if binding is None:
            return RequestResolution(
                request_uid=request.request_uid,
                request_version=request.version,
                status=RequestStatus.unresolved,
                reason="import_failed",
                warnings=warnings,
                search_uid=search.search_uid,
            ), reports, search
        return RequestResolution(
            request_uid=request.request_uid,
            request_version=request.version,
            status=RequestStatus.resolved,
            binding=binding,
            warnings=warnings + binding.warnings,
            search_uid=search.search_uid,
        ), reports, search

    # ------------------------------------------------------------------
    # 检索执行
    # ------------------------------------------------------------------

    def _provider_search(
        self, provider: AssetProvider, query: ProviderQuery, ctx: SearchContext
    ) -> Tuple[List[AssetCandidate], ProviderResult]:
        """在线源走 Search Cache（§69）；其余直接调用。"""

        if provider.source_kind == "online":
            key = search_cache_key(
                provider.provider_id, model_dump(query))
            cached = self.cache.search_get(key)
            if cached is not None:
                candidates = [model_validate(AssetCandidate, c) for c in cached]
                return candidates, ProviderResult(
                    status=ProviderStatus.success if candidates
                    else ProviderStatus.no_result,
                    candidates=candidates, message="cache_hit")
        result = provider.safe_search(query, ctx)
        if provider.source_kind == "online" and result.status == ProviderStatus.success:
            key = search_cache_key(
                provider.provider_id, model_dump(query))
            self.cache.search_put(key, [model_dump(c) for c in result.candidates])
        return result.candidates, result

    def _prepare_batch(
        self, batch: List[AssetCandidate], request: AssetRequest, search: SearchRecord
    ) -> List[AssetCandidate]:
        """normalize → inspect（本地文件真实检）→ dedup → hard filter。"""

        normalized, rej = normalize_batch(batch)
        search.rejected_candidates.extend(rej)
        for cand in normalized:
            self._inspect_candidate_inplace(cand)
        unique, rej = dedup_candidates(normalized)
        search.rejected_candidates.extend(rej)
        passed, rej = hard_filter(unique, request)
        search.rejected_candidates.extend(rej)
        search.candidates.extend(passed)
        return passed

    def _inspect_candidate_inplace(self, candidate: AssetCandidate) -> None:
        """本地可读候选做真实检测；在线候选只标记未验证（§24/§42）。"""

        path = candidate.local_uri or (
            candidate.original_uri
            if candidate.source_type != AssetSource.online else "")
        if not path or not os.path.isfile(path):
            candidate.provenance["inspected"] = False
            candidate.technical_metadata.partial = True
            return
        result = self.inspector.inspect_file(path)
        candidate.technical_metadata = _merge_technical(
            result.technical, candidate.technical_metadata)
        candidate.provenance["inspected"] = True
        candidate.provenance["decodable"] = result.integrity.decodable
        candidate.provenance["content_hash"] = result.integrity.content_hash
        candidate.provenance["perceptual_hash"] = result.integrity.perceptual_hash

    # ------------------------------------------------------------------
    # 选择 / 导入 / 绑定
    # ------------------------------------------------------------------

    def _select_and_bind(
        self,
        request: AssetRequest,
        collected: List[AssetCandidate],
        ranked: List[Any],
        search: SearchRecord,
        warnings: List[str],
    ) -> Optional[BindingRecord]:
        by_uid = {c.candidate_uid: c for c in collected}
        order = [r.candidate_uid for r in ranked]
        alternatives = order[1:4]  # §50：保留 Top alternatives
        last_error = ""
        for idx, cand_uid in enumerate(order[:_MAX_IMPORT_RETRIES]):
            candidate = by_uid.get(cand_uid)
            if candidate is None:
                continue
            record, error = self._import_candidate(candidate, request)
            if record is None:
                last_error = error or "import_failed"
                search.rejected_candidates.append(RejectedCandidate(
                    candidate_uid=cand_uid, reason="import_failed",
                    detail=last_error))
                continue
            search.selected_candidate = cand_uid
            row = ranked[idx]
            binding = self.binding_mgr.create(
                request, record, score=row.score,
                breakdown=row.breakdown, alternatives=alternatives,
                warnings=list(warnings), fallback_used=idx > 0,
            )
            self._touch_usage(request, record.asset_uid)
            return binding
        if last_error:
            warnings.append(last_error)
        return None

    def _import_candidate(
        self, candidate: AssetCandidate, request: AssetRequest
    ) -> Tuple[Optional[AssetRecord], str]:
        """Import Pipeline（§52）：取文件 → decode → hash → registry。"""

        provider = self._provider_for(candidate)
        try:
            if candidate.local_uri and os.path.isfile(candidate.local_uri):
                path = candidate.local_uri
            else:
                dest = self.store.downloaded_dir
                os.makedirs(dest, exist_ok=True)
                path = provider.download(candidate, dest) if provider else candidate.original_uri
        except Exception as exc:  # noqa: BLE001
            return None, f"download_failed:{type(exc).__name__}"
        if not path or not os.path.isfile(path):
            return None, "file_missing"
        inspected = self.inspector.inspect_file(path)
        if not inspected.integrity.decodable:
            return None, "not_decodable"
        if not self._import_still_valid(candidate, inspected.technical, request):
            return None, "verify_failed"
        record = self.registry.register_candidate(
            candidate, local_uri=path, integrity=inspected.integrity,
            scope=RegistryScope.project)
        record.technical_metadata = _merge_technical(
            inspected.technical, record.technical_metadata)
        return record, ""

    def _import_still_valid(
        self, candidate: AssetCandidate, tech: TechnicalMetadata, request: AssetRequest
    ) -> bool:
        """下载后复核（§42）：声称 alpha 但实际无 → 拒收。"""

        required, _p = split_technical(request.technical_requirements)
        if required.get("has_alpha") and tech.has_alpha is False:
            return False
        if required.get("min_width") and tech.width and tech.width < int(required["min_width"]):
            return False
        if required.get("min_height") and tech.height and tech.height < int(required["min_height"]):
            return False
        return True

    def _provider_for(self, candidate: AssetCandidate) -> Optional[AssetProvider]:
        for provider in self.providers.values():
            if candidate.provider_id == provider.provider_id or \
                    candidate.provider_id.startswith(provider.provider_id + ":"):
                return provider
        return None

    # ------------------------------------------------------------------
    # 用户素材导入（§19/§35/§52）
    # ------------------------------------------------------------------

    def import_user_asset(
        self,
        path: str,
        caption: str = "",
        tags: Optional[List[str]] = None,
        asset_type: str = "image",
        media_type: str = "",
    ) -> AssetRecord:
        """用户上传 → 统一 Import Pipeline → project scope + source=user。"""

        self.store.ensure_dirs()
        name = os.path.basename(path)
        dest = os.path.join(self.store.imported_dir, name)
        if os.path.abspath(path) != os.path.abspath(dest):
            shutil.copyfile(path, dest)
        inspected = self.inspector.inspect_file(dest)
        if not media_type:
            media_type = "audio" if inspected.technical.mime_type.startswith("audio") else "image"
        if asset_type == "image" and media_type == "audio":
            asset_type = "music"
        candidate = AssetCandidate(
            candidate_uid=f"import_{inspected.integrity.content_hash[:8]}",
            provider_id="user_upload",
            source_type=AssetSource.user,
            source_ref=dest,
            asset_type=asset_type,
            media_type=media_type,
            preview_uri=dest,
            original_uri=dest,
            local_uri=dest,
            semantic_metadata=SemanticMetadata(
                caption=caption, attributes=list(tags or [])),
            technical_metadata=inspected.technical,
            license_metadata=LicenseMetadata(type="user_provided"),
        )
        record = self.registry.register_candidate(
            candidate, local_uri=dest, integrity=inspected.integrity,
            scope=RegistryScope.project)
        record.technical_metadata = _merge_technical(
            inspected.technical, record.technical_metadata)
        self._persist()
        return record

    # ------------------------------------------------------------------
    # alternatives 切换（§85）
    # ------------------------------------------------------------------

    def switch_alternative(self, request_uid: str) -> Optional[AssetBinding]:
        """换一个：优先用候选池留存的 alternative，不重新联网搜。"""

        binding = self.binding_mgr.active_for(request_uid)
        search = self._search_pool.get(request_uid) or self._load_last_search(request_uid)
        if binding is None or search is None or not binding.alternatives:
            return None
        next_uid = binding.alternatives[0]
        by_uid = {c.candidate_uid: c for c in search.candidates}
        candidate = by_uid.get(next_uid)
        if candidate is None:
            return None
        request = AssetRequest(
            request_uid=request_uid,
            version=binding.request_version,
            asset_type=candidate.asset_type or "image",
            media_type=candidate.media_type or "image",
        )
        record, error = self._import_candidate(candidate, request)
        if record is None:
            binding.alternatives.pop(0)
            binding.warnings.append(f"alternative_failed:{next_uid}:{error}")
            return self.switch_alternative(request_uid)
        remaining = binding.alternatives[1:] + [search.selected_candidate]
        row = next(
            (r for r in search.ranked_candidates if r.candidate_uid == next_uid), None)
        new_binding = self.binding_mgr.create(
            request, record,
            score=row.score if row else 0.0,
            breakdown=row.breakdown if row else {},
            alternatives=remaining,
        )
        self._touch_usage(request, record.asset_uid)
        self._persist()
        return self.binding_mgr.to_planner(new_binding, record)

    def _load_last_search(self, request_uid: str) -> Optional[SearchRecord]:
        for raw in reversed(self.store.search_history()):
            if raw.get("request_uid") == request_uid:
                return model_validate(SearchRecord, raw)
        return None

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _context(self, request: AssetRequest) -> SearchContext:
        return SearchContext(
            project_id=self.store.project_id,
            request_uid=request.request_uid,
            asset_type=request.asset_type,
            media_type=request.media_type,
            limit=self.candidate_limit,
        )

    def _new_search(self, request: AssetRequest) -> SearchRecord:
        self._search_seq += 1
        return SearchRecord(
            search_uid=f"srch_{self._search_seq:04d}",
            request_uid=request.request_uid,
            request_version=request.version,
        )

    @staticmethod
    def _report(provider: AssetProvider, result: ProviderResult) -> ProviderReport:
        return ProviderReport(
            provider_id=provider.provider_id,
            status=result.status,
            candidate_count=len(result.candidates),
            message=result.message,
        )

    def _maybe_audio_dependency(
        self, request: AssetRequest, candidates: List[AssetCandidate]
    ) -> Optional[AssetDependencyRequest]:
        """§47/§84：音乐需要 BPM/时长而候选缺数据时产出依赖请求。"""

        if request.asset_type not in _MUSIC_TYPES:
            return None
        tech = request.technical_requirements or {}
        needs = [k for k in ("bpm_range", "min_duration", "max_duration") if tech.get(k)]
        if not needs:
            return None
        lacking = [
            c for c in candidates
            if ("bpm_range" in needs and c.technical_metadata.bpm is None)
            or (("min_duration" in needs or "max_duration" in needs)
                and c.technical_metadata.duration is None)
        ]
        if not lacking:
            return None
        self._dep_seq += 1
        analyses: List[str] = []
        if "bpm_range" in needs:
            analyses.append("bpm")
        if "min_duration" in needs or "max_duration" in needs:
            analyses.append("duration")
        return AssetDependencyRequest(
            request_uid=f"adep_{self._dep_seq:03d}",
            type="audio_analysis",
            asset_ref=lacking[0].original_uri or lacking[0].source_ref,
            required_analyses=analyses,
            reason="candidate metadata missing for music ranking",
            blocking=False,
            request_ref=request.request_uid,
        )

    def _touch_usage(self, request: AssetRequest, asset_uid: str) -> None:
        ctx = request.usage_context or {}
        items = [str(i) for i in ctx.get("plan_items", [])]
        if ctx.get("plan_item"):
            items.append(str(ctx["plan_item"]))
        for item in items:
            usage_index_ops.record_usage(self.state.usage_index, asset_uid, item)

    def _top_status(
        self, result: AssetResolutionResult, requests: List[AssetRequest]
    ) -> ResolutionStatus:
        if not requests:
            return ResolutionStatus.resolved
        by_uid = {r.request_uid: r for r in result.resolutions}
        resolved = 0
        hard_failed = 0
        warned = bool(result.provider_warnings or result.dependency_requests)
        for req in requests:
            res = by_uid.get(req.request_uid)
            if res is not None and res.status == RequestStatus.resolved:
                resolved += 1
                warned = warned or bool(res.warnings)
            elif req.constraint_level == ConstraintLevel.hard:
                hard_failed += 1
            else:
                warned = True  # §68：soft 未解 → skip/warning，不拖垮整体
        if hard_failed and resolved:
            return ResolutionStatus.partial
        if hard_failed:
            return ResolutionStatus.failed
        if warned:
            return ResolutionStatus.resolved_with_warnings
        return ResolutionStatus.resolved

    def _persist(self, result: Optional[AssetResolutionResult] = None) -> None:
        self.state.registry = self.registry.records
        self.state.bindings = self.binding_mgr._bindings
        self.store.save_state(self.state)
        if result is not None:
            for record in result.search_records:
                self.store.append_search(record)


def _merge_technical(
    measured: TechnicalMetadata, claimed: TechnicalMetadata
) -> TechnicalMetadata:
    """实测字段覆盖，claimed 补实测没有的（如 manifest 声明的 bpm）。"""

    out = model_validate(TechnicalMetadata, model_dump(measured))
    for field in ("bpm", "duration"):
        if getattr(out, field) is None and getattr(claimed, field) is not None:
            setattr(out, field, getattr(claimed, field))
    if not out.format and claimed.format:
        out.format = claimed.format
    out.partial = measured.partial
    return out
