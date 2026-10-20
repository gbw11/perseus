"""고정 Xen libxenmanage의 도메인 unique_id를 읽는다. Xen 변경·자원 할당은 하지 않는다.

ABI: Xen 1c72306b1f0b4dc533954f678361044f8083dccc tools/include/xenmanage.h.
별도 프로세스로 호출하여 상위 관측의 전체 마감시간을 적용한다.
"""
import argparse
import ctypes
import json
import os
import sys


def identity(domid, library=None):
    if type(domid) is not int or not 0 < domid < 0x7ff0:
        raise ValueError("valid guest domid required")
    lib = library if library is not None else ctypes.CDLL("libxenmanage.so.1", use_errno=True)
    lib.xenmanage_open.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    lib.xenmanage_open.restype = ctypes.c_void_p
    lib.xenmanage_close.argtypes = [ctypes.c_void_p]
    lib.xenmanage_close.restype = ctypes.c_int
    lib.xenmanage_get_domain_info.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint64)]
    lib.xenmanage_get_domain_info.restype = ctypes.c_int
    handle = lib.xenmanage_open(None, 0)
    if not handle:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    try:
        state, caps, unique = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint64()
        if lib.xenmanage_get_domain_info(handle, domid, ctypes.byref(state),
                                        ctypes.byref(caps), ctypes.byref(unique)):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        if not state.value & 1 or not unique.value:
            raise ValueError("live domain incarnation could not be verified")
        # JSON/JavaScript consumers must not round a 64-bit incarnation number.
        return {"domid": domid, "xen_unique_id": str(unique.value), "state_bits": state.value}
    finally:
        lib.xenmanage_close(handle)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domid", required=True, type=int)
    args = parser.parse_args(argv)
    print(json.dumps(identity(args.domid)))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print("[xenova-domain-identity] " + type(error).__name__ + ": " + str(error), file=sys.stderr)
        raise SystemExit(1)
