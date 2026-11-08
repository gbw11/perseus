"""합성 procfs bytes의 bounded 문법 시험. 실제 PID 생존·namespace·mount 관측 시험은 아니다."""

from dataclasses import FrozenInstanceError
import unittest

from xenova_vehicle.runtime.procfs_formats import (
    MountRecord, ProcFormatError, ProcStat, parse_boot_id, parse_fdinfo,
    parse_mountinfo, parse_proc_stat, validate_self_status,
)


_BOOT = b"00000000-0000-4000-8000-000000000001"
_U64 = 2**64 - 1
_U32 = 2**32 - 1


def _stat(*, pid=1234, comm=b"oak-worker", state=b"S", start=42, numeric=None):
    values = [b"0"] * 49 if numeric is None else list(numeric)
    if numeric is None:
        values[18] = str(start).encode("ascii")  # field4부터 시작하므로 field22는 19번째다.
    return str(pid).encode("ascii") + b" (" + comm + b") " + state + b" " + b" ".join(values) + b"\n"


def _status(pid=1234):
    value = str(pid).encode("ascii")
    return b"Name:\topaque\\nname\xff\nPid:\t" + value + b"\nTgid:\t" + value + b"\nNSpid:\t" + value + b"\nNStgid:\t" + value + b"\nThreads:\t7\n"


def _mount_row(*, mount=22, parent=1, device=b"0:20", root=b"/", point=b"/proc", options=b"rw,nosuid",
               optional=(), fs=b"proc", source=b"proc", super_options=b"rw"):
    left = [str(mount).encode("ascii"), str(parent).encode("ascii"), device, root, point, options]
    return b" ".join(left + list(optional) + [b"-", fs, source, super_options]) + b"\n"


def _sized_mount_row(length, mount=22):
    base = _mount_row(mount=mount, optional=(b"future:",))
    return _mount_row(mount=mount, optional=(b"future:" + b"x" * (length - len(base)),))


class ProcStatFormatTests(unittest.TestCase):
    def test_complete_kernel_profile_preserves_pid_state_and_original_clock_ticks(self):
        self.assertEqual(parse_proc_stat(_stat(), 1234), ProcStat(1234, "S", 42))

    def test_opaque_comm_spaces_parentheses_newline_and_non_utf8_do_not_shift_field22(self):
        for comm in (b"", b"oak worker", b"oak) (worker", b")", b"(nested))(", b"oak\nworker", b"oak\xff"):
            with self.subTest(comm=comm):
                self.assertEqual(parse_proc_stat(_stat(comm=comm), 1234).start_ticks, 42)

    def test_comm_15_raw_byte_boundary_preserves_empty_and_opaque_names(self):
        for comm in (b"", b"x" * 15, b"a" * 11 + b"\n)\xff(", b"\xff" * 15):
            with self.subTest(comm=comm):
                self.assertLessEqual(len(comm), 15)
                self.assertEqual(parse_proc_stat(_stat(comm=comm), 1234), ProcStat(1234, "S", 42))
        for comm in (b"x" * 16, b"\xc3\xa9" * 8):
            with self.subTest(comm=comm), self.assertRaises(ProcFormatError):
                parse_proc_stat(_stat(comm=comm), 1234)

    def test_comm_internal_nul_is_rejected_even_inside_raw_byte_limit(self):
        for comm in (b"\x00", b"x" * 7 + b"\x00" + b"x" * 7,
                     b"x" * 14 + b"\x00", b"\x00" + b"x" * 14):
            with self.subTest(comm=comm), self.assertRaises(ProcFormatError):
                parse_proc_stat(_stat(comm=comm), 1234)

    def test_two_concatenated_records_cannot_be_absorbed_as_one_opaque_comm(self):
        # 독립 원 STAT19를 열람하지 않고 보완 계약에 적힌 두 record 구조만 새로 만든다.
        first = _stat(pid=4242, comm=b"first", start=42)
        for second_pid in (4242, 4321):
            second = _stat(pid=second_pid, comm=b"second", start=77)
            with self.subTest(second_pid=second_pid), self.assertRaises(ProcFormatError):
                parse_proc_stat(first + second, 4242)

    def test_signed_unrelated_fields_zero_and_full_unsigned_fields_remain_legal(self):
        values = [b"0"] * 49
        values[0] = b"-1"
        values[1] = b"-9223372036854775808"
        values[5] = str(_U64).encode("ascii")
        values[18] = b"0"
        self.assertEqual(parse_proc_stat(_stat(numeric=values), 1234), ProcStat(1234, "S", 0))

    def test_start_ticks_u64_boundaries_are_not_defaulted_or_signed(self):
        self.assertEqual(parse_proc_stat(_stat(start=_U64), 1234).start_ticks, _U64)
        for token in (b"-1", str(_U64 + 1).encode("ascii"), b"9" * 2000, b"+1", b"1.0"):
            values = [b"0"] * 49
            values[18] = token
            with self.subTest(token=token[:32]), self.assertRaises(ProcFormatError):
                parse_proc_stat(_stat(numeric=values), 1234)

    def test_supported_seven_states_include_zombie_and_dead_without_exit_classification(self):
        for state in b"RSDTtXZ":
            self.assertEqual(parse_proc_stat(_stat(state=bytes([state])), 1234).state, chr(state))
        for state in (b"I", b"P", b"x", b"SS", b"", b"\xff"):
            with self.subTest(state=state), self.assertRaises(ProcFormatError):
                parse_proc_stat(_stat(state=state), 1234)

    def test_exact_52_fields_and_final_lf_are_required(self):
        raw = _stat()
        variants = (raw[:-1], raw + b"\n", raw[:-1] + b"\r\n",
                    _stat(numeric=[b"0"] * 48), _stat(numeric=[b"0"] * 50), b"1234 (a) S\n")
        for value in variants:
            with self.subTest(value=value[:40]), self.assertRaises(ProcFormatError):
                parse_proc_stat(value, 1234)

    def test_pid_prefix_comm_envelope_and_expected_pid_cannot_be_substituted(self):
        raw = _stat()
        variants = (raw.replace(b"1234 (", b"4321 (", 1), raw.replace(b"1234 (", b"1234[", 1),
                    raw.replace(b") S ", b"] S ", 1), b" " + raw, raw.replace(b"1234", b"-1", 1))
        for value in variants:
            with self.assertRaises(ProcFormatError):
                parse_proc_stat(value, 1234)
        for expected in (False, 0, -1, _U32 + 1, "1234"):
            with self.subTest(expected=expected), self.assertRaises(ProcFormatError):
                parse_proc_stat(raw, expected)

    def test_unrelated_numeric_overflow_or_non_decimal_is_rejected(self):
        for token in (b"-9223372036854775809", str(_U64 + 1).encode("ascii"), b"-" + b"1" * 100,
                      b"nan", b"0x10", b"1_000", b"+2"):
            values = [b"0"] * 49
            values[4] = token
            with self.subTest(token=token[:30]), self.assertRaises(ProcFormatError):
                parse_proc_stat(_stat(numeric=values), 1234)

    def test_stat_raw_io_cap_does_not_make_long_comm_schema_valid(self):
        self.assertEqual(parse_proc_stat(_stat(comm=b"x" * 15), 1234).start_ticks, 42)
        empty = _stat(comm=b"")
        exact = _stat(comm=b"x" * (4096 - len(empty)))
        self.assertEqual(len(exact), 4096)
        # 형식 음성만 확인한다. syscall의 4096/cap+1 수집 경계는 native 시험의 별도 책임이다.
        for raw in (exact, _stat(comm=b"x" * (4097 - len(empty)))):
            with self.subTest(raw_bytes=len(raw)), self.assertRaises(ProcFormatError):
                parse_proc_stat(raw, 1234)


class ProcAuxiliaryFormatTests(unittest.TestCase):
    def test_boot_id_is_exact_canonical_uuid_with_kernel_final_lf(self):
        self.assertEqual(parse_boot_id(_BOOT + b"\n"), _BOOT.decode("ascii"))
        for raw in (_BOOT, _BOOT + b"\r\n", b" " + _BOOT + b"\n", _BOOT.upper() + b"a\n",
                    b"g" + _BOOT[1:] + b"\n", b"x" * 65):
            with self.subTest(raw=raw), self.assertRaises(ProcFormatError):
                parse_boot_id(raw)
        with self.assertRaises(ProcFormatError):
            parse_boot_id(b"abcdefab-cdef-abcd-efab-abcdefabcdef\n".upper())

    def test_fdinfo_requires_one_mount_id_without_new_kernel_ino_field(self):
        self.assertEqual(parse_fdinfo(b"pos:\t0\nflags:\t0100000\nmnt_id:\t22\n"), 22)
        self.assertEqual(parse_fdinfo(b"mnt_id:\t" + str(_U64).encode("ascii") + b"\nunknown:\topaque\xff\n"), _U64)

    def test_fdinfo_missing_duplicate_malformed_or_out_of_range_mount_id_is_rejected(self):
        for raw in (b"pos:\t0\n", b"mnt_id:\t22\nmnt_id:\t22\n", b"mnt_id:\t22 22\n",
                    b"mnt_id:\t0\n", b"mnt_id:\t-1\n", b"mnt_id:\t+1\n",
                    b"mnt_id:\t" + str(_U64 + 1).encode("ascii") + b"\n",
                    b"mnt_id:\t" + b"1" * 5000 + b"\n", b"mnt_id:\t22"):
            with self.subTest(raw=raw[:45]), self.assertRaises(ProcFormatError):
                parse_fdinfo(raw)

    def test_fdinfo_8192_raw_cap_is_inclusive(self):
        prefix = b"mnt_id:\t22\nopaque:\t"
        exact = prefix + b"x" * (8192 - len(prefix) - 1) + b"\n"
        self.assertEqual(parse_fdinfo(exact), 22)
        with self.assertRaises(ProcFormatError):
            parse_fdinfo(prefix + b"x" * (8193 - len(prefix) - 1) + b"\n")

    def test_self_status_validates_single_same_view_without_requiring_pid1_ns_or_other_fields(self):
        self.assertIsNone(validate_self_status(_status(), 1234))
        minimal = b"Pid: 1234\nTgid: 1234\nNSpid: 1234\nNStgid: 1234\n"
        self.assertIsNone(validate_self_status(minimal, 1234))

    def test_each_self_identity_field_must_exist_once_even_if_duplicates_are_equal(self):
        raw = _status()
        for key in (b"Pid", b"Tgid", b"NSpid", b"NStgid"):
            line = key + b":\t1234\n"
            with self.subTest(key=key, mode="missing"), self.assertRaises(ProcFormatError):
                validate_self_status(raw.replace(line, b"", 1), 1234)
            with self.subTest(key=key, mode="duplicate"), self.assertRaises(ProcFormatError):
                validate_self_status(raw + line, 1234)

    def test_same_numeric_ids_on_two_namespace_levels_are_not_single_level_proof(self):
        for key in (b"NSpid", b"NStgid"):
            raw = _status().replace(key + b":\t1234\n", key + b":\t1234\t1234\n")
            with self.subTest(key=key), self.assertRaises(ProcFormatError):
                validate_self_status(raw, 1234)

    def test_self_status_different_process_pid_range_or_truncation_is_not_repaired(self):
        for key in (b"Pid", b"Tgid", b"NSpid", b"NStgid"):
            for value in (b"4321", b"0", b"-1", b"+1234", str(_U32 + 1).encode("ascii"), b"nan"):
                raw = _status().replace(key + b":\t1234\n", key + b":\t" + value + b"\n")
                with self.subTest(key=key, value=value), self.assertRaises(ProcFormatError):
                    validate_self_status(raw, 1234)
        with self.assertRaises(ProcFormatError):
            validate_self_status(_status()[:-1], 1234)

    def test_self_status_65536_raw_cap_is_inclusive(self):
        prefix = _status() + b"Opaque: "
        exact = prefix + b"x" * (65536 - len(prefix) - 1) + b"\n"
        self.assertIsNone(validate_self_status(exact, 1234))
        with self.assertRaises(ProcFormatError):
            validate_self_status(prefix + b"x" * (65537 - len(prefix) - 1) + b"\n", 1234)


class MountInfoFormatTests(unittest.TestCase):
    def test_required_fields_have_native_numbers_owned_bytes_and_option_tuples(self):
        rows = parse_mountinfo(_mount_row())
        self.assertEqual(rows, (MountRecord(22, 1, 0, 20, b"/", b"/proc", b"proc", b"proc",
                                            (b"rw", b"nosuid"), (b"rw",), ()),))

    def test_actual_nano_root_mount_row_has_nonnegative_parent_zero(self):
        # Nano attempt02가 회수한 원 행이다. 이 literal의 parse는 새 OS 관측이 아니다.
        raw = b"21 0 179:1 / / rw,relatime shared:1 - ext4 /dev/mmcblk0p1 rw,data=ordered\n"
        self.assertEqual(parse_mountinfo(raw), (
            MountRecord(21, 0, 179, 1, b"/", b"/", b"ext4", b"/dev/mmcblk0p1",
                        (b"rw", b"relatime"), (b"rw", b"data=ordered"), (b"shared:1",)),
        ))

    def test_stacked_mountpoints_unknown_optional_and_unrelated_non_utf8_paths_are_legal(self):
        first = _mount_row(mount=21, point=b"/proc")
        second = _mount_row(mount=22, point=b"/proc", optional=(b"shared:9", b"future:tag"))
        third = _mount_row(mount=23, root=b"/opaque\xff", point=b"/elsewhere\xfe", source=b"device\xff")
        rows = parse_mountinfo(first + second + third)
        self.assertEqual(tuple(row.mount_id for row in rows), (21, 22, 23))
        self.assertEqual(rows[1].optional_fields, (b"shared:9", b"future:tag"))
        self.assertEqual(rows[2].mountpoint, b"/elsewhere\xfe")

    def test_four_path_escapes_are_decoded_once_after_tokenization(self):
        encoded = b"/a\\040b\\011c\\012d\\134040"
        decoded = b"/a b\tc\nd\\040"
        row = parse_mountinfo(_mount_row(root=encoded, point=encoded, source=encoded))[0]
        self.assertEqual((row.root, row.mountpoint, row.source), (decoded, decoded, decoded))

    def test_literal_dash_source_is_not_mistaken_for_second_separator(self):
        self.assertEqual(parse_mountinfo(_mount_row(source=b"-"))[0].source, b"-")

    def test_duplicate_mount_id_is_rejected_globally_not_only_on_selected_path(self):
        raw = _mount_row(mount=22) + _mount_row(mount=22, point=b"/unrelated", fs=b"tmpfs")
        with self.assertRaises(ProcFormatError):
            parse_mountinfo(raw)

    def test_mount_numeric_profile_accepts_u64_ids_u32_device_zero_and_rejects_overflow(self):
        row = parse_mountinfo(_mount_row(mount=_U64, parent=_U64, device=b"0:0"))[0]
        self.assertEqual((row.mount_id, row.parent_id, row.major, row.minor), (_U64, _U64, 0, 0))
        self.assertEqual(parse_mountinfo(_mount_row(device=str(_U32).encode() + b":" + str(_U32).encode()))[0].major, _U32)
        for raw in (_mount_row(mount=0), _mount_row(parent=-1), _mount_row(mount=_U64 + 1),
                    _mount_row(parent=_U64 + 1),
                    _mount_row(device=b"-1:0"), _mount_row(device=b"0:4294967296"),
                    _mount_row(device=b"1:2:3"), _mount_row(device=b"+1:2")):
            with self.subTest(raw=raw[:55]), self.assertRaises(ProcFormatError):
                parse_mountinfo(raw)

    def test_missing_fields_bad_separator_empty_options_or_unfinished_rows_are_rejected(self):
        good = _mount_row()
        variants = (b"", good[:-1], good + b"\n", good.replace(b" - ", b" ? "),
                    good.replace(b" - ", b" - - "), good.replace(b"rw,nosuid", b"rw,,nosuid"),
                    good.replace(b"/ /proc", b"relative /proc"), good[:-1] + b"\r\n",
                    b"22 1 0:20 / /proc rw - proc proc\n")
        for raw in variants:
            with self.subTest(raw=raw[:70]), self.assertRaises(ProcFormatError):
                parse_mountinfo(raw)

    def test_mount_row_65536_and_4096_rows_caps_are_inclusive(self):
        exact = _sized_mount_row(65536)
        self.assertEqual(len(exact), 65536)
        self.assertEqual(len(parse_mountinfo(exact)), 1)
        with self.assertRaises(ProcFormatError):
            parse_mountinfo(_sized_mount_row(65537))
        rows = b"".join(_mount_row(mount=index + 1) for index in range(4096))
        self.assertEqual(len(parse_mountinfo(rows)), 4096)
        with self.assertRaises(ProcFormatError):
            parse_mountinfo(rows + _mount_row(mount=4097))

    def test_mountinfo_one_mib_raw_cap_is_inclusive_without_ignoring_last_row(self):
        exact = b"".join(_sized_mount_row(65536, mount=index + 1) for index in range(16))
        self.assertEqual(len(exact), 1048576)
        self.assertEqual(len(parse_mountinfo(exact)), 16)
        with self.assertRaises(ProcFormatError):
            parse_mountinfo(exact + b"\n")


class ProcFormatNativeBoundaryTests(unittest.TestCase):
    def test_all_raw_entrypoints_reject_non_exact_bytes_without_invoking_native_hooks(self):
        calls = []

        class TrapMeta(type):
            def __eq__(self, other):
                calls.append("type.eq")
                raise AssertionError("native type comparison must not call metaclass hook")

        class Trap(metaclass=TrapMeta):
            def __bytes__(self):
                calls.append("bytes")
                raise AssertionError("conversion hook must not run")

        class BytesSubclass(bytes):
            pass

        functions = (lambda raw: parse_proc_stat(raw, 1234), parse_boot_id, parse_fdinfo,
                     lambda raw: validate_self_status(raw, 1234), parse_mountinfo)
        for entry in functions:
            for raw in (Trap(), BytesSubclass(b"x\n"), bytearray(b"x\n"), memoryview(b"x\n"), "x\n", None):
                with self.subTest(entry=entry, raw_type=type(raw).__name__), self.assertRaises(ProcFormatError):
                    entry(raw)
        self.assertEqual(calls, [])

    def test_public_records_are_frozen_and_validate_exact_native_field_types(self):
        parsed = parse_proc_stat(_stat(), 1234)
        mounted = parse_mountinfo(_mount_row())[0]
        with self.assertRaises(FrozenInstanceError):
            parsed.start_ticks = 99
        with self.assertRaises(FrozenInstanceError):
            mounted.root = b"/changed"
        for args in ((False, "S", 0), (0, "S", 0), (1, b"S", 0), (1, "I", 0), (1, "S", -1), (1, "S", _U64 + 1)):
            with self.assertRaises(ProcFormatError):
                ProcStat(*args)
        valid = [22, 1, 0, 20, b"/", b"/proc", b"proc", b"proc", (b"rw",), (b"rw",), ()]
        for position, value in ((0, False), (2, -1), (4, bytearray(b"/")), (8, [b"rw"]), (10, ("shared:1",))):
            args = list(valid)
            args[position] = value
            with self.subTest(position=position), self.assertRaises(ProcFormatError):
                MountRecord(*args)


if __name__ == "__main__":
    unittest.main()
