"""MemoryBackend（§42）：验证模块五 Core 的全模拟 Backend。

状态：execution/backend/memory/<project_id>/sim.json
    {revision, tracks, objects, media_refs, saved}
支持 CREATE/UPDATE/DELETE/NOOP 全操作、revision、失败注入
（fail_plan）与 capability override——大多数单元测试都在此上完成。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from gesture_intent.models import model_dump, model_validate

from ..compiler.fingerprint import object_fingerprint
from ..dsl.models import (
    BackendCapabilityManifest,
    BackendExecutionReport,
    BackendInfo,
    BackendSession,
    BackendVerificationReport,
    CapabilityStatus,
    EditOpType,
    ExportResult,
    OperationResult,
    OperationStatus,
)
from ..dsl.scheduler import topo_order
from ..models import (
    BackendConfig,
    BackendProjectRef,
    DesiredProjectGraph,
    ExecutionPatch,
    TimelineObject,
    _stable_uid,
)
from .base import BackendOperationError, ExecutorBackend, capability_manifest

_ALL_CAPS = [
    "tracking",
    "keyframes",
    "overlay",
    "text",
    "audio",
    "freeze",
    "scale_animation",
    "position_animation",
    "background_replacement",
    "opacity",
    "person_mask",
    "foreground_subject_mask",
    "camera_motion",
]


class MemoryBackend(ExecutorBackend):
    def __init__(
        self,
        sim_dir: Optional[Path] = None,
        *,
        config: Optional[BackendConfig] = None,
        fail_plan: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.sim_dir = Path(sim_dir) if sim_dir is not None else None
        self.config = config or BackendConfig(backend_id="memory")
        self.fail_plan = dict(fail_plan or {})
        self._sim: Optional[Dict[str, Any]] = None
        self._verify_fail_next = bool(self.fail_plan.get("verify"))

    # ---- sim 持久化 ----

    def _sim_path(self) -> Optional[Path]:
        if self.sim_dir is None:
            return None
        return self.sim_dir / "sim.json"

    def _load_sim(self) -> Dict[str, Any]:
        if self._sim is not None:
            return self._sim
        path = self._sim_path()
        if path is not None and path.exists():
            self._sim = json.loads(path.read_text(encoding="utf-8"))
        else:
            self._sim = {
                "revision": 0,
                "tracks": {},
                "objects": {},
                "media_refs": {},
                "saved": False,
            }
        return self._sim

    def _save_sim(self) -> None:
        path = self._sim_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(self._sim, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        import os

        os.replace(tmp, path)

    # ---- §47 接口 ----

    def backend_info(self) -> BackendInfo:
        from .. import __version__

        return BackendInfo(backend_id="memory", backend_version=__version__)

    def describe_capabilities(self) -> BackendCapabilityManifest:
        supported = {
            name: CapabilityStatus.supported.value for name in _ALL_CAPS
        }
        for name, status in self.config.capabilities_override.items():
            supported[name] = status
        return capability_manifest("memory", supported)

    def begin_session(self, project_context: Dict[str, Any]) -> BackendSession:
        return BackendSession(
            session_uid=_stable_uid(
                "sess", project_context.get("project_id", ""), "memory"
            ),
            project_id=str(project_context.get("project_id", "")),
            mode=self.config.materialization_mode,
        )

    def execute(
        self,
        session: BackendSession,
        patch: Optional[ExecutionPatch],
        desired_graph: DesiredProjectGraph,
    ) -> BackendExecutionReport:
        sim = self._load_sim()
        report = BackendExecutionReport()
        if session.mode == "rebuild":
            self._materialize(sim, desired_graph)
            self._save_sim()
            return report
        if patch is None:
            report.error = "incremental 模式需要 patch"
            return report
        ordered = topo_order(patch.operations)
        failed_uids: List[str] = []
        skipped_uids: set = set()
        for index, op in enumerate(ordered):
            result = OperationResult(operation_uid=op.operation_uid)
            # 依赖失败/被跳过 → skipped（级联，§64）
            if set(op.depends_on) & (set(failed_uids) | skipped_uids):
                op.status = OperationStatus.skipped.value
                result.status = OperationStatus.skipped.value
                skipped_uids.add(op.operation_uid)
                report.op_results.append(result)
                continue
            try:
                self._maybe_fail(index, op)
                self._apply_op(sim, op)
                op.status = OperationStatus.completed.value
                result.status = OperationStatus.completed.value
            except BackendOperationError as exc:
                op.status = OperationStatus.failed.value
                result.status = OperationStatus.failed.value
                result.error = str(exc)
                failed_uids.append(op.operation_uid)
            report.op_results.append(result)
        sim["revision"] = patch.target_revision
        self._save_sim()
        if failed_uids:
            report.error = f"{len(failed_uids)} operation(s) failed"
        return report

    def verify(
        self, session: BackendSession, desired_graph: DesiredProjectGraph
    ) -> BackendVerificationReport:
        report = BackendVerificationReport()
        if self._verify_fail_next:
            self._verify_fail_next = False
            report.ok = False
            report.issues.append("verify: 注入失败")
            return report
        sim = self._load_sim()
        desired_uids = set(desired_graph.objects)
        sim_uids = set(sim.get("objects", {}))
        if sim_uids != desired_uids:
            report.ok = False
            report.issues.append(
                f"对象集不一致 missing={sorted(desired_uids - sim_uids)} "
                f"extra={sorted(sim_uids - desired_uids)}"
            )
            return report
        for uid in sorted(desired_uids):
            stored = sim["objects"][uid]
            desired_fp = desired_graph.objects[uid].fingerprint
            if stored.get("fingerprint") != desired_fp:
                report.ok = False
                report.issues.append(f"{uid}: fingerprint 不一致")
        return report

    def save(self, session: BackendSession) -> BackendProjectRef:
        if self.fail_plan.get("save"):
            raise BackendOperationError("save: 注入失败")
        sim = self._load_sim()
        path = self._sim_path()
        return BackendProjectRef(
            backend_id="memory",
            project_revision=int(sim.get("revision") or 0),
            local_uri=str(path) if path is not None else None,
            created_at="",
            valid=True,
        )

    def export(self, session: BackendSession, export_spec: Any) -> ExportResult:
        return ExportResult(status="unsupported")

    # ---- 内部 ----

    def _materialize(
        self, sim: Dict[str, Any], desired_graph: DesiredProjectGraph
    ) -> None:
        sim["tracks"] = {
            uid: model_dump(t) for uid, t in desired_graph.tracks.items()
        }
        sim["objects"] = {
            uid: model_dump(o) for uid, o in desired_graph.objects.items()
        }
        sim["media_refs"] = {
            uid: model_dump(m) for uid, m in desired_graph.media_refs.items()
        }
        sim["saved"] = True

    def _maybe_fail(self, index: int, op) -> None:
        plan = self.fail_plan
        if "op_index" in plan and index == int(plan["op_index"]):
            raise BackendOperationError(f"注入失败 op_index={index}")
        if "op_type" in plan and op.op_type == plan["op_type"]:
            raise BackendOperationError(f"注入失败 op_type={op.op_type}")
        if "after_ops" in plan and index > int(plan["after_ops"]):
            raise BackendOperationError(f"注入失败 after_ops={plan['after_ops']}")

    def _apply_op(self, sim: Dict[str, Any], op) -> None:
        t = op.op_type
        args = op.arguments
        if t == EditOpType.create_project.value:
            sim.update(
                {
                    "project_spec": args.get("project_spec", {}),
                    "saved": False,
                }
            )
        elif t == EditOpType.open_project.value:
            pass
        elif t == EditOpType.import_media.value:
            sim["media_refs"][op.target_uid] = dict(args)
        elif t == EditOpType.ensure_track.value:
            sim["tracks"][op.target_uid] = dict(args)
        elif t == EditOpType.create_object.value:
            self._store_object(sim, op.target_uid, dict(args))
        elif t == EditOpType.delete_object.value:
            sim["objects"].pop(op.target_uid, None)
        elif t == EditOpType.replace_media.value:
            updates = {"media_ref": args.get("media_ref")}
            if "asset_uid" in args:
                updates["asset_uid"] = args["asset_uid"]
            self._mutate(sim, op, updates)
        elif t == EditOpType.set_time_range.value:
            self._mutate(
                sim,
                op,
                {"project_time": {"start": args.get("start"), "end": args.get("end")}},
            )
        elif t == EditOpType.set_transform.value:
            spec = self._object_spec(sim, op.target_uid)
            transform = dict(spec.get("transform") or {})
            for key in ("position", "scale", "rotation"):
                if key in args and args[key] is not None:
                    transform[key] = args[key]
            spec["transform"] = transform or None
            self._store_object(sim, op.target_uid, spec)
        elif t == EditOpType.set_animation.value:
            self._mutate(sim, op, {"animation": dict(args)})
        elif t == EditOpType.set_keyframes.value:
            self._mutate(sim, op, {"keyframes": list(args.get("keyframes") or [])})
        elif t == EditOpType.set_mask.value:
            self._mutate(sim, op, {"mask_ref": args.get("artifact_uid")})
        elif t in (
            EditOpType.set_text.value,
            EditOpType.set_volume.value,
            EditOpType.freeze_frame.value,
        ):
            spec = self._object_spec(sim, op.target_uid)
            parameters = dict(spec.get("parameters") or {})
            parameters.update({k: v for k, v in args.items() if v is not None})
            spec["parameters"] = parameters
            self._store_object(sim, op.target_uid, spec)
        elif t == EditOpType.save_project.value:
            sim["saved"] = True
        elif t == EditOpType.export_video.value:
            pass  # MVP：导出未实现，op 视为完成
        else:
            raise BackendOperationError(f"未知 op_type: {t}")

    def _object_spec(self, sim: Dict[str, Any], uid: str) -> Dict[str, Any]:
        if uid not in sim["objects"]:
            raise BackendOperationError(f"对象不存在: {uid}")
        return dict(sim["objects"][uid])

    def _mutate(self, sim: Dict[str, Any], op, updates: Dict[str, Any]) -> None:
        spec = self._object_spec(sim, op.target_uid)
        spec.update(updates)
        self._store_object(sim, op.target_uid, spec)

    def _store_object(self, sim: Dict[str, Any], uid: str, spec: Dict[str, Any]) -> None:
        """落盘前重建模型并重算 fingerprint（与 desired 保持同一口径）。"""
        spec["timeline_object_uid"] = uid
        obj = model_validate(TimelineObject, spec)
        obj.fingerprint = object_fingerprint(obj)
        sim["objects"][uid] = model_dump(obj)
