"""RGB/native depth와 고정 YOLOP를 한 Device/graph에서 수집하는 선택 벤치.

같은 ColorCamera.video를 NN에도 보내 원 입력 key를 대조한다. tensor 해석·
빈공간·주행 허가를 만들지 않으며 no-NN OakSource의 장치 수명을 재사용한다.
"""

from dataclasses import replace
import json
import math
import operator
import struct
import time

from .device import OakSource, _pipeline, _us
from .yolop_asset import ASSET_ID, load_yolop_asset, validated_sdk_blob


def compact_yolop_graph(pipeline, asset_receipt: dict, fps: int) -> dict:
    """실제 node/connection만 읽는다. serializeToJson·properties·asset storage는 접근하지 않는다.

    고정 SDK의 전체 graph JSON에는 모델 바이트까지 담기므로 이를 만든 뒤 잘라내는
    방식도 사용하지 않는다. 설정은 host 선언이며 device 설정 readback이 아니다.
    """
    def text(value, name):
        if type(value) is not str or len(value) > 128:
            raise ValueError(f"invalid compact graph {name}")
        return value

    def node_id(value):
        if type(value) is not int or not 0 <= value < 2**63:
            raise ValueError("invalid compact graph node ID")
        return value

    native_nodes = pipeline.getAllNodes()
    if not 1 <= len(native_nodes) <= 16:
        raise ValueError("YOLOP graph exceeds its node bound")
    nodes = [{"id": node_id(node.id), "name": text(node.getName(), "node name")}
             for node in native_nodes]
    ids = {node["id"] for node in nodes}
    if len(ids) != len(nodes):
        raise ValueError("duplicate compact graph node IDs")
    groups = pipeline.getConnectionMap()
    if len(groups) > 16:
        raise ValueError("YOLOP graph exceeds its connection-group bound")
    connections = []
    for group in groups.values():
        if len(group) > 32 or len(connections) + len(group) > 32:
            raise ValueError("YOLOP graph exceeds its connection bound")
        for connection in group:
            item = {"output_id": node_id(connection.outputId),
                    "output_name": text(connection.outputName, "output name"),
                    "output_group": text(connection.outputGroup, "output group"),
                    "input_id": node_id(connection.inputId),
                    "input_name": text(connection.inputName, "input name"),
                    "input_group": text(connection.inputGroup, "input group")}
            if item["output_id"] not in ids or item["input_id"] not in ids:
                raise ValueError("compact graph connection references an absent node")
            connections.append(item)
    return {"scope": "compact_host_node_connection_declaration", "asset_storage_included": False,
            "device_config_readback": False,
            "nodes": sorted(nodes, key=lambda node: node["id"]),
            "connections": sorted(connections, key=lambda item: (item["output_id"], item["output_group"],
                                   item["output_name"], item["input_id"], item["input_group"], item["input_name"])),
            "openvino_version": text(str(pipeline.getOpenVINOVersion()), "OpenVINO version"),
            "declared_settings": {"fps_requested": fps,
                                  "rgb": {"socket": "CAM_A", "size_wh": [640, 360], "type": "NV12"},
                                  "depth": {"size_wh": [640, 480], "alignment": "RECTIFIED_RIGHT",
                                            "unit": "millimeter", "lr_check": True,
                                            "extended_disparity": False, "subpixel": False},
                                  "nn": {"type": "BGR888p", "size_wh": [320, 320],
                                         "content_size_wh": [320, 180], "padding_ltrb": [0, 70, 0, 70],
                                         "padding_bgr": [0, 0, 0], "inference_threads": 1}},
            "asset": {key: asset_receipt[key] for key in ("asset_id", "blob_sha256", "blob_bytes")}}


class YolopOakSource(OakSource):
    """A가 넘긴 immutable context와 검증 asset을 한 session 동안 유지한다."""

    def __init__(self, profile, *, context_key: str):
        super().__init__(profile)
        if (type(context_key) is not str or len(context_key) != 64
                or any(char not in "0123456789abcdef" for char in context_key)):
            raise ValueError("YOLOP context_key must be the parent's canonical SHA256")
        self.context_key = context_key
        self._asset = None
        self._blob = None
        self._blob_metadata = None
        self._blob_registration_count = 0
        self._golden_done = False
        self._nn_failure = None
        self._nn_failure_captured = False

    def take_failure_diagnostic(self) -> dict | None:
        """최초 NN 변환 실패의 소유 bytes/metadata를 한 번 넘기고 소스 참조를 놓는다."""
        diagnostic = self._nn_failure
        self._nn_failure = None
        return diagnostic

    def _remember_nn_failure(self, message, received_j_us: int, error: Exception) -> None:
        """검증 실패를 교정하지 않고 원 SDK metadata/최대1MiB bytes만 격리 보존한다."""
        if self._nn_failure_captured:
            return
        self._nn_failure_captured = True

        def kind(value):
            return type(value).__name__[:64]

        def scalar(value):
            if type(value) is int:
                if value.bit_length() <= 64:
                    return value
                return {"opaque": "integer_exceeds_64_bits", "bits": value.bit_length()}
            if type(value) is str:
                if len(value) <= 128:
                    return value
                return {"opaque": "text_exceeds_128_chars", "length": len(value)}
            return {"opaque": "unsupported_scalar", "python_type": kind(value)}

        def array(value):
            if type(value) not in (list, tuple):
                return {"opaque": "unsupported_array", "python_type": kind(value)}
            return {"values": [scalar(item) for item in value[:4]],
                    "length": len(value), "truncated": len(value) > 4}

        def read(getter, convert=scalar):
            try:
                return convert(getter())
            except Exception as exc:
                return {"opaque": "unreadable_field", "error_type": kind(exc)}

        def enum(value):
            return {"code": read(lambda: operator.index(value)),
                    "name": read(lambda: value.name), "python_type": kind(value)}

        metadata = {"schema_version": 1, "scope": "yolop-independent-failure-diagnostic",
                    "stream": "nn_output", "context_key": self.context_key,
                    "record_only": True, "qualification": "unvalidated_original_bytes_and_metadata",
                    "nn_ready": False, "metric_ready": False, "exception_type": kind(error),
                    "received_j_us": scalar(received_j_us),
                    "sequence": read(lambda: message.getSequenceNum()),
                    "device_timestamp_us": read(lambda: _us(message.getTimestampDevice())),
                    "sdk_timestamp_us": read(lambda: _us(message.getTimestamp())),
                    "tensors": []}
        payloads = {}
        try:
            infos = message.getRaw().tensors
            if type(infos) not in (list, tuple):
                metadata["tensors_error"] = {"opaque": "unsupported_tensor_sequence",
                                              "python_type": kind(infos)}
            else:
                metadata["tensor_count"] = len(infos)
                metadata["tensors_truncated"] = len(infos) > 3
                for info in infos[:3]:
                    metadata["tensors"].append({
                        "name": read(lambda: info.name), "dataType": read(lambda: info.dataType, enum),
                        "order": read(lambda: info.order, enum),
                        "numDimensions": read(lambda: info.numDimensions),
                        "dims": read(lambda: info.dims, array),
                        "strides": read(lambda: info.strides, array),
                        "offset": read(lambda: info.offset)})
        except Exception as exc:
            metadata["tensors_error"] = {"opaque": "unreadable_tensor_metadata", "error_type": kind(exc)}
        try:
            view = memoryview(message.getData())
            metadata["original_payload_bytes"] = scalar(view.nbytes)
            if view.nbytes > 1_048_576:
                metadata["payload_rejection"] = "payload_exceeds_1MiB; not_copied"
            elif (view.ndim != 1 or view.itemsize != 1 or view.format != "B"
                  or not view.c_contiguous):
                metadata["payload_rejection"] = "invalid_uint8_buffer_shape_or_format; not_copied"
            else:
                payloads["nn-buffer.bin"] = bytes(view)
        except Exception as exc:
            metadata["payload_rejection"] = "unreadable_buffer; not_copied"
            metadata["payload_error_type"] = kind(exc)
        encoded = json.dumps(metadata, ensure_ascii=True, allow_nan=False,
                             sort_keys=True, separators=(",", ":")).encode("ascii")
        if len(encoded) > 32 * 1024:
            # 생략 자체도 명시한다. 크기 상한을 넘기는 opaque SDK 값을 stringify하지 않는다.
            metadata = {"schema_version": 1, "scope": "yolop-independent-failure-diagnostic",
                        "record_only": True, "qualification": "unvalidated_original_bytes_and_metadata",
                        "stream": "nn_output", "context_key": self.context_key,
                        "exception_type": kind(error), "metadata_rejection": "exceeds_32KiB",
                        "nn_ready": False, "metric_ready": False}
        self._nn_failure = {"metadata": metadata, "payloads": payloads}

    def start(self, on_stage) -> dict:
        if self._started or self._asset is not None:
            raise RuntimeError("a YOLOP source cannot be started twice")
        on_stage("ASSET_CHECK")
        self._asset = load_yolop_asset(self.profile.asset_dir, self.profile.asset_manifest)
        return super().start(on_stage)

    def _build_pipeline(self, dai):
        if self._asset is None:
            raise RuntimeError("YOLOP asset validation must precede graph registration")
        self._blob, self._blob_metadata = validated_sdk_blob(dai, self._asset)

        def extend(pipeline, color, stereo):
            pipeline.setOpenVINOVersion(dai.OpenVINO.Version.VERSION_2021_4)
            manip = pipeline.create(dai.node.ImageManip)
            manip.initialConfig.setResizeThumbnail(320, 320, 0, 0, 0)
            manip.initialConfig.setFrameType(dai.ImgFrame.Type.BGR888p)
            manip.setMaxOutputFrameSize(320 * 320 * 3)
            manip.setNumFramesPool(2)
            manip.setWaitForConfigInput(False)
            manip.inputImage.setQueueSize(1)
            manip.inputImage.setBlocking(False)
            network = pipeline.create(dai.node.NeuralNetwork)
            network.setBlob(self._blob)
            self._blob_registration_count += 1
            network.setNumInferenceThreads(1)
            network.setNumPoolFrames(2)
            network.input.setQueueSize(1)
            network.input.setBlocking(False)
            color.video.link(manip.inputImage)
            manip.out.link(network.input)
            for name, output, metadata_only in (("nn_input", network.passthrough, True),
                                                 ("nn_output", network.out, False)):
                xout = pipeline.create(dai.node.XLinkOut)
                xout.setStreamName(name)
                xout.setMetadataOnly(metadata_only)
                xout.input.setQueueSize(1)
                xout.input.setBlocking(False)
                output.link(xout.input)
            required = str(pipeline.getRequiredOpenVINOVersion())
            expected = self._asset.manifest["sdk_blob_metadata"]["host_graph_required_version"]
            if required != expected or pipeline.getOpenVINOVersion() != dai.OpenVINO.Version.VERSION_2021_4:
                raise ValueError("YOLOP graph and blob OpenVINO versions differ")

        return _pipeline(dai, self.profile.fps, graph_extension=extend)

    def _queue_sizes(self) -> dict[str, int]:
        return {"rgb_video": 8, "depth_right": 8, "nn_input": 8, "nn_output": 2}

    def _graph_declaration(self, pipeline) -> dict:
        return compact_yolop_graph(pipeline, self._asset.receipt, self.profile.fps)

    def _decorate_receipt(self, receipt: dict) -> dict:
        receipt.update({"nn_requirement": "REQUIRED", "nn_context_key": self.context_key,
                        "nn_ready": False, "model_id": ASSET_ID,
                        "pipeline_start_count": 1,
                        "blob_registration_count": self._blob_registration_count,
                        "model_asset": self._asset.receipt,
                        "sdk_blob_metadata": self._blob_metadata,
                        "model_residency_evidence": "host_verified_asset_and_graph_start_only",
                        "preprocessing": self._asset.manifest["preprocessing_profile"],
                        "nn": {"inference_threads": 1, "pool_frames": 2,
                               "input_queue_size": 1, "input_queue_blocking": False,
                               "input_source": "same_CAM_A_video_as_rgb_video",
                               "input_type": "BGR888p", "input_size_wh": [320, 320],
                               "passthrough_metadata_only": True,
                               "pixel_equivalence_verified": False,
                               "sdk_layer_golden_policy": "first_valid_packet_only",
                               "constructor": "verified_bytes_to_temporary_uint8_list"},
                        "queues": {"host_sizes": self._queue_sizes(), "host_blocking": False,
                                   "device_xlink_size": 1, "device_xlink_blocking": False,
                                   "collector_messages_per_stream": 1,
                                   "collector_payload_cap_bytes": 4 * 1024**2}})
        return receipt

    def _sdk_golden(self, packet, message) -> dict:
        """검증된 첫 packet만 SDK layer accessor와 대조한다. 추가 복사 비용도 계측한다."""
        from .yolop_frames import validate_nn_packet

        started = time.monotonic_ns() // 1000
        layers = []
        total = 0
        accessor_calls = 0
        try:
            # 빈 stride/범위 밖 metadata로 C++ accessor를 먼저 호출하지 않는다.
            validate_nn_packet(packet)
            for name in ("det_out", "drive_area_seg", "lane_line_seg"):
                owned = packet.layer_bytes(name)
                count = len(owned) // 2
                total += count
                if len(owned) % 2 or total > 500_000:
                    raise ValueError("invalid golden element budget")
                accessor_calls += 1
                values = message.getLayerFp16(name)
                matched = type(values) is list and len(values) == count
                if matched:
                    matched = all(math.isfinite(value) and value == reference[0]
                                  for value, reference in zip(values, struct.iter_unpack("<e", owned),
                                                              strict=True))
                layers.append({"name": name, "elements": count, "matched": matched})
                del values
            result = {"performed": True, "matched": all(item["matched"] for item in layers),
                      "elements": total, "layers": layers,
                      "comparison": "finite_FP16_values_exact_SDK_accessor_vs_owned_bytes"}
        except Exception as exc:
            result = {"performed": True, "matched": False, "elements": total, "layers": layers,
                      "error_type": type(exc).__name__}
        result["elapsed_us"] = time.monotonic_ns() // 1000 - started
        result["accessor_calls"] = accessor_calls
        return result

    def poll(self) -> list:
        """4 stream에서 하나씩만 복사한다. SDK 객체·view는 이 호출 밖으로 빌려주지 않는다."""
        from .yolop_frames import sdk_nn_input, sdk_nn_packet

        result = []
        copied = 0
        for stream, queue in self._queues.items():
            message = queue.tryGet()
            if message is None:
                continue
            if stream in ("rgb_video", "depth_right"):
                frame = self._frame(stream, message)
            else:
                now = time.monotonic_ns() // 1000
                convert = sdk_nn_input if stream == "nn_input" else sdk_nn_packet
                try:
                    frame = convert(message, now, self.context_key)
                except Exception as exc:
                    if stream == "nn_output":
                        try:
                            self._remember_nn_failure(message, now, exc)
                        except Exception as diagnostic_error:
                            self._nn_failure = {"metadata": {
                                "schema_version": 1, "scope": "yolop-independent-failure-diagnostic",
                                "stream": "nn_output", "context_key": self.context_key,
                                "record_only": True, "qualification": "unvalidated_original_metadata_unavailable",
                                "exception_type": type(exc).__name__[:64],
                                "diagnostic_error_type": type(diagnostic_error).__name__[:64],
                                "nn_ready": False, "metric_ready": False}, "payloads": {}}
                    raise
                current = (frame.sequence, frame.device_us, frame.sdk_us)
                if stream in self._last and any(a <= b for a, b in zip(current, self._last[stream])):
                    raise ValueError(f"non-increasing YOLOP sequence/timestamp in {stream}")
                self._last[stream] = current
                if stream == "nn_output" and not self._golden_done:
                    frame = replace(frame, golden=self._sdk_golden(frame, message))
                    self._golden_done = True
            copied += len(getattr(frame, "payload", b""))
            if copied > 4 * 1024**2:
                raise ValueError("YOLOP collector exceeded bounded copy budget")
            result.append(frame)
        return result

    def close(self) -> None:
        """Device close 성공 뒤 host asset 참조만 놓는다. 별도 NN unload 성공을 주장하지 않는다."""
        super().close()
        self._blob = None
        self._asset = None
        self._blob_metadata = None
        self._nn_failure = None
