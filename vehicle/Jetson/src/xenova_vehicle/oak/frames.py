"""SDK 객체의 수명에서 분리한 원본 프레임. 거리·빈공간 판정은 포함하지 않는다."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RawFrame:
    """한 stream의 소유한 bytes와 원 시각(us). stream 간 sequence는 독립이다."""

    stream: str
    sequence: int
    device_us: int
    sdk_us: int
    received_j_us: int
    width: int
    height: int
    encoding: str
    payload: bytes
    exposure_us: int | None = None

    def validate(self) -> None:
        if self.stream not in ("rgb_video", "depth_right"):
            raise ValueError("unsupported capture stream")
        for name in ("sequence", "device_us", "sdk_us", "received_j_us"):
            if type(getattr(self, name)) is not int or not 0 <= getattr(self, name) < 2**64:
                raise ValueError(f"invalid {name}")
        if (type(self.width) is not int or type(self.height) is not int
                or not 0 < self.width <= 640 or not 0 < self.height <= 480):
            raise ValueError("invalid frame dimensions")
        if self.stream == "rgb_video":
            if self.encoding != "NV12" or self.width % 2 or self.height % 2:
                raise ValueError("RGB capture requires even-sized NV12")
            size = self.width * self.height * 3 // 2
        else:
            if self.encoding != "RAW16_LE":
                raise ValueError("native depth requires little-endian uint16")
            size = self.width * self.height * 2
        if type(self.payload) is not bytes or len(self.payload) != size:
            raise ValueError("frame payload length/type mismatch")
        if self.exposure_us is not None and (type(self.exposure_us) is not int
                                            or not 0 <= self.exposure_us <= 1_000_000):
            raise ValueError("invalid exposure duration")

    def descriptor(self) -> dict:
        """파일 기록용 형식 명세. native depth의 zero는 invalid이며 FREE가 아니다."""
        self.validate()
        return {"stream": self.stream, "sequence": str(self.sequence),
                "device_timestamp_us": str(self.device_us),
                "sdk_timestamp_us": str(self.sdk_us),
                "received_j_us": str(self.received_j_us),
                "width": self.width, "height": self.height, "encoding": self.encoding,
                "dtype": "uint8" if self.stream == "rgb_video" else "<u2",
                "row_stride_bytes": self.width if self.stream == "rgb_video" else self.width * 2,
                "payload_length": len(self.payload), "exposure_us": self.exposure_us,
                "depth_unit": "millimeter" if self.stream == "depth_right" else None,
                "invalid_depth_value": 0 if self.stream == "depth_right" else None}
