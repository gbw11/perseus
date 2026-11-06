"""Follow the existing private VM snapshot; never query Xen or renew its clock."""
import stat


class SharedFollower:
    def __init__(self, reader):
        self.reader = reader
        self.version = None

    def read_changed(self):
        info = self.reader.path.lstat()
        version = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns,
                   info.st_size, info.st_uid, stat.S_IMODE(info.st_mode))
        if version == self.version:
            # StateStore derives expiry from the original observed_mono even
            # when this file has not changed. No repeated JSON parse or refresh.
            return None
        result = self.reader.read()  # Existing ownership/boot/age/schema checks.
        self.version = version
        return result

    def invalidate(self):
        self.version = None
