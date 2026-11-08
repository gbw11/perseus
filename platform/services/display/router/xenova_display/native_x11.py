"""Read-only Linux 64-bit X11 shared-memory capture, private UID-owned segment.

API references: https://www.x.org/releases/X11R7.7/doc/xextproto/shm.html
XImage ABI: https://gitlab.freedesktop.org/xorg/lib/libx11/-/blob/master/include/X11/Xlib.h
"""
import ctypes as C
import os

class Image(C.Structure):
    _fields_ = [('width', C.c_int), ('height', C.c_int), ('xoffset', C.c_int), ('format', C.c_int), ('data', C.c_void_p), ('byte_order', C.c_int), ('bitmap_unit', C.c_int), ('bitmap_bit_order', C.c_int), ('bitmap_pad', C.c_int), ('depth', C.c_int), ('bytes_per_line', C.c_int), ('bits_per_pixel', C.c_int), ('red_mask', C.c_ulong), ('green_mask', C.c_ulong), ('blue_mask', C.c_ulong), ('obdata', C.c_void_p), ('functions', C.c_void_p * 6)]

class Segment(C.Structure):
    _fields_ = [('shmseg', C.c_ulong), ('shmid', C.c_int), ('shmaddr', C.c_void_p), ('read_only', C.c_int)]

class SharedCapture:

    def __init__(self, width, height, x=0, y=0, *, display=None):
        if not (1 <= width <= 1920 and 1 <= height <= 1080 and (0 <= x <= 3840) and (0 <= y <= 2160)):
            raise ValueError('invalid capture rectangle')
        self.x, self.y = (x, y)
        self.display = None
        self.image = None
        self.address = None
        self.shmid = -1
        self.attached = False
        self.failed = False
        self.x11 = C.CDLL('libX11.so.6')
        self.ext = C.CDLL('libXext.so.6')
        self.libc = C.CDLL(None, use_errno=True)

        def bind(lib, name, args, result):
            fn = getattr(lib, name)
            fn.argtypes = args
            fn.restype = result
            return fn
        self.open = bind(self.x11, 'XOpenDisplay', [C.c_char_p], C.c_void_p)
        self.close_display = bind(self.x11, 'XCloseDisplay', [C.c_void_p], C.c_int)
        self.sync = bind(self.x11, 'XSync', [C.c_void_p, C.c_int], C.c_int)
        screen = bind(self.x11, 'XDefaultScreen', [C.c_void_p], C.c_int)
        visual = bind(self.x11, 'XDefaultVisual', [C.c_void_p, C.c_int], C.c_void_p)
        depth = bind(self.x11, 'XDefaultDepth', [C.c_void_p, C.c_int], C.c_int)
        root = bind(self.x11, 'XDefaultRootWindow', [C.c_void_p], C.c_ulong)
        screen_width = bind(self.x11, 'XDisplayWidth', [C.c_void_p, C.c_int], C.c_int)
        screen_height = bind(self.x11, 'XDisplayHeight', [C.c_void_p, C.c_int], C.c_int)
        query = bind(self.ext, 'XShmQueryExtension', [C.c_void_p], C.c_int)
        create = bind(self.ext, 'XShmCreateImage', [C.c_void_p, C.c_void_p, C.c_uint, C.c_int, C.c_void_p, C.POINTER(Segment), C.c_uint, C.c_uint], C.POINTER(Image))
        self.attach = bind(self.ext, 'XShmAttach', [C.c_void_p, C.POINTER(Segment)], C.c_int)
        self.detach = bind(self.ext, 'XShmDetach', [C.c_void_p, C.POINTER(Segment)], C.c_int)
        self.get = bind(self.ext, 'XShmGetImage', [C.c_void_p, C.c_ulong, C.POINTER(Image), C.c_int, C.c_int, C.c_ulong], C.c_int)
        shmget = bind(self.libc, 'shmget', [C.c_int, C.c_size_t, C.c_int], C.c_int)
        shmat = bind(self.libc, 'shmat', [C.c_int, C.c_void_p, C.c_int], C.c_void_p)
        self.shmdt = bind(self.libc, 'shmdt', [C.c_void_p], C.c_int)
        self.shmctl = bind(self.libc, 'shmctl', [C.c_int, C.c_int, C.c_void_p], C.c_int)
        handler_type = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)
        self.error_handler = handler_type(lambda *_: setattr(self, 'failed', True) or 0)
        self.set_error_handler = bind(self.x11, 'XSetErrorHandler', [C.c_void_p], C.c_void_p)
        self.previous_handler = self.set_error_handler(C.cast(self.error_handler, C.c_void_p))
        try:
            if not (C.sizeof(Image) == 136 and C.sizeof(Segment) == 32):
                raise RuntimeError('unsupported X11 ABI')
            self.display = self.open((display or os.environ.get('DISPLAY', ':0')).encode())
            if not self.display:
                raise RuntimeError('cannot open authenticated X11 display')
            if not query(self.display):
                raise RuntimeError('MIT-SHM is unavailable')
            index = screen(self.display)
            self.root = root(self.display)
            self.segment = Segment()
            if not (x + width <= screen_width(self.display, index) and y + height <= screen_height(self.display, index)):
                raise RuntimeError('capture rectangle is outside the current X11 root')
            self.image = create(self.display, visual(self.display, index), depth(self.display, index), 2, None, C.byref(self.segment), width, height)
            if not self.image:
                raise RuntimeError('XShmCreateImage failed')
            image = self.image.contents
            if not (image.width, image.height, image.bits_per_pixel, image.byte_order) == (width, height, 32, 0):
                raise RuntimeError('unsupported X11 pixel packing')
            if not (image.red_mask, image.green_mask, image.blue_mask) == (16711680, 65280, 255):
                raise RuntimeError('unsupported X11 channel order')
            if not width * 4 <= image.bytes_per_line <= width * 4 + 256:
                raise RuntimeError('invalid X11 capture state')
            self.pitch = image.bytes_per_line
            size = self.pitch * height
            self.shmid = shmget(0, size, 0o1000 | 0o600)  # IPC_CREAT, owner access only
            if self.shmid < 0:
                raise RuntimeError(os.strerror(C.get_errno()))
            self.address = shmat(self.shmid, None, 0)
            if self.address in (None, C.c_void_p(-1).value):
                raise RuntimeError(os.strerror(C.get_errno()))
            self.segment.shmid = self.shmid
            self.segment.shmaddr = self.address
            self.segment.read_only = 0
            self.image.contents.data = self.address
            if not self.attach(self.display, C.byref(self.segment)):
                raise RuntimeError('XShmAttach failed')
            self.attached = True
            self.sync(self.display, 0)
            if self.failed:
                raise RuntimeError('X server rejected private shared memory')
            if self.shmctl(self.shmid, 0, None) != 0:  # IPC_RMID
                raise RuntimeError('cannot mark shared segment for removal')
        except BaseException:
            self.close()
            raise

    def frame(self):
        if not self.display or not self.image:
            raise RuntimeError('X11 capture is closed')
        self.failed = False
        if not self.get(self.display, self.root, self.image, self.x, self.y, C.c_ulong(-1)) or self.failed:
            raise RuntimeError('XShmGetImage failed')
        return self.address

    def close(self):
        if self.display and self.attached:
            self.detach(self.display, C.byref(self.segment))
            self.sync(self.display, 0)
            self.attached = False
        if self.image:
            destroy = C.CFUNCTYPE(C.c_int, C.POINTER(Image))(self.image.contents.functions[1])
            destroy(self.image)
            self.image = None
        if self.address and self.address != C.c_void_p(-1).value:
            self.shmdt(self.address)
            self.address = None
        if self.shmid >= 0:
            self.shmctl(self.shmid, 0, None)
            self.shmid = -1
        if self.display:
            self.close_display(self.display)
            self.display = None
        if hasattr(self, 'previous_handler'):
            self.set_error_handler(self.previous_handler)
            del self.previous_handler

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class WindowAttributes(C.Structure):
    _fields_ = [('background_pixmap', C.c_ulong), ('background_pixel', C.c_ulong), ('border_pixmap', C.c_ulong), ('border_pixel', C.c_ulong), ('bit_gravity', C.c_int), ('win_gravity', C.c_int), ('backing_store', C.c_int), ('backing_planes', C.c_ulong), ('backing_pixel', C.c_ulong), ('save_under', C.c_int), ('event_mask', C.c_long), ('do_not_propagate_mask', C.c_long), ('override_redirect', C.c_int), ('colormap', C.c_ulong), ('cursor', C.c_ulong)]

class Color(C.Structure):
    _fields_ = [('pixel', C.c_ulong), ('red', C.c_ushort), ('green', C.c_ushort), ('blue', C.c_ushort), ('flags', C.c_char), ('pad', C.c_char)]

CW_BACK_PIXEL, CW_OVERRIDE_REDIRECT = 1 << 1, 1 << 9


class SharedPresenter:
    """411: show BGRX frames in one override-redirect window through MIT-SHM.

    The decoder writes a frame into `address` (row stride `pitch`), then
    `present()` copies it to the window. One X11 connection, used only by
    the thread that created it. No pointer cursor is shown on the output.
    """

    def __init__(self, width, height, x=0, y=0, *, display=None):
        if not (1 <= width <= 1920 and 1 <= height <= 1080 and (0 <= x <= 3840) and (0 <= y <= 2160)):
            raise ValueError('invalid output rectangle')
        self.width, self.height = width, height
        self.display = None
        self.window = 0
        self.gc = None
        self.font = 0
        self.cursor = 0
        self.image = None
        self.address = None
        self.shmid = -1
        self.attached = False
        self.failed = False
        self.x11 = C.CDLL('libX11.so.6')
        self.ext = C.CDLL('libXext.so.6')
        self.libc = C.CDLL(None, use_errno=True)

        def bind(lib, name, args, result):
            fn = getattr(lib, name)
            fn.argtypes = args
            fn.restype = result
            return fn
        X = self.x11
        self.open = bind(X, 'XOpenDisplay', [C.c_char_p], C.c_void_p)
        self.close_display = bind(X, 'XCloseDisplay', [C.c_void_p], C.c_int)
        self.sync = bind(X, 'XSync', [C.c_void_p, C.c_int], C.c_int)
        screen = bind(X, 'XDefaultScreen', [C.c_void_p], C.c_int)
        visual = bind(X, 'XDefaultVisual', [C.c_void_p, C.c_int], C.c_void_p)
        depth = bind(X, 'XDefaultDepth', [C.c_void_p, C.c_int], C.c_int)
        root = bind(X, 'XDefaultRootWindow', [C.c_void_p], C.c_ulong)
        screen_width = bind(X, 'XDisplayWidth', [C.c_void_p, C.c_int], C.c_int)
        screen_height = bind(X, 'XDisplayHeight', [C.c_void_p, C.c_int], C.c_int)
        create_window = bind(X, 'XCreateWindow', [C.c_void_p, C.c_ulong, C.c_int, C.c_int, C.c_uint, C.c_uint, C.c_uint, C.c_int, C.c_uint, C.c_void_p, C.c_ulong, C.POINTER(WindowAttributes)], C.c_ulong)
        self.destroy_window = bind(X, 'XDestroyWindow', [C.c_void_p, C.c_ulong], C.c_int)
        map_raised = bind(X, 'XMapRaised', [C.c_void_p, C.c_ulong], C.c_int)
        create_gc = bind(X, 'XCreateGC', [C.c_void_p, C.c_ulong, C.c_ulong, C.c_void_p], C.c_void_p)
        self.free_gc = bind(X, 'XFreeGC', [C.c_void_p, C.c_void_p], C.c_int)
        self.set_foreground = bind(X, 'XSetForeground', [C.c_void_p, C.c_void_p, C.c_ulong], C.c_int)
        self.fill = bind(X, 'XFillRectangle', [C.c_void_p, C.c_ulong, C.c_void_p, C.c_int, C.c_int, C.c_uint, C.c_uint], C.c_int)
        self.draw_string = bind(X, 'XDrawString', [C.c_void_p, C.c_ulong, C.c_void_p, C.c_int, C.c_int, C.c_char_p, C.c_int], C.c_int)
        load_font = bind(X, 'XLoadFont', [C.c_void_p, C.c_char_p], C.c_ulong)
        set_font = bind(X, 'XSetFont', [C.c_void_p, C.c_void_p, C.c_ulong], C.c_int)
        self.unload_font = bind(X, 'XUnloadFont', [C.c_void_p, C.c_ulong], C.c_int)
        bitmap = bind(X, 'XCreateBitmapFromData', [C.c_void_p, C.c_ulong, C.c_char_p, C.c_uint, C.c_uint], C.c_ulong)
        free_pixmap = bind(X, 'XFreePixmap', [C.c_void_p, C.c_ulong], C.c_int)
        pixmap_cursor = bind(X, 'XCreatePixmapCursor', [C.c_void_p, C.c_ulong, C.c_ulong, C.POINTER(Color), C.POINTER(Color), C.c_uint, C.c_uint], C.c_ulong)
        define_cursor = bind(X, 'XDefineCursor', [C.c_void_p, C.c_ulong, C.c_ulong], C.c_int)
        self.free_cursor = bind(X, 'XFreeCursor', [C.c_void_p, C.c_ulong], C.c_int)
        query = bind(self.ext, 'XShmQueryExtension', [C.c_void_p], C.c_int)
        create = bind(self.ext, 'XShmCreateImage', [C.c_void_p, C.c_void_p, C.c_uint, C.c_int, C.c_void_p, C.POINTER(Segment), C.c_uint, C.c_uint], C.POINTER(Image))
        attach = bind(self.ext, 'XShmAttach', [C.c_void_p, C.POINTER(Segment)], C.c_int)
        self.detach = bind(self.ext, 'XShmDetach', [C.c_void_p, C.POINTER(Segment)], C.c_int)
        self.put = bind(self.ext, 'XShmPutImage', [C.c_void_p, C.c_ulong, C.c_void_p, C.POINTER(Image), C.c_int, C.c_int, C.c_int, C.c_int, C.c_uint, C.c_uint, C.c_int], C.c_int)
        shmget = bind(self.libc, 'shmget', [C.c_int, C.c_size_t, C.c_int], C.c_int)
        shmat = bind(self.libc, 'shmat', [C.c_int, C.c_void_p, C.c_int], C.c_void_p)
        self.shmdt = bind(self.libc, 'shmdt', [C.c_void_p], C.c_int)
        self.shmctl = bind(self.libc, 'shmctl', [C.c_int, C.c_int, C.c_void_p], C.c_int)
        handler_type = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)
        self.error_handler = handler_type(lambda *_: setattr(self, 'failed', True) or 0)
        self.set_error_handler = bind(X, 'XSetErrorHandler', [C.c_void_p], C.c_void_p)
        self.previous_handler = self.set_error_handler(C.cast(self.error_handler, C.c_void_p))
        try:
            if not (C.sizeof(Image) == 136 and C.sizeof(Segment) == 32 and C.sizeof(WindowAttributes) == 112 and C.sizeof(Color) == 16):
                raise RuntimeError('unsupported X11 ABI')
            self.display = self.open((display or os.environ.get('DISPLAY', ':0')).encode())
            if not self.display:
                raise RuntimeError('cannot open authenticated X11 display')
            if not query(self.display):
                raise RuntimeError('MIT-SHM is unavailable')
            index = screen(self.display)
            if not (x + width <= screen_width(self.display, index) and y + height <= screen_height(self.display, index)):
                raise RuntimeError('output rectangle is outside the current X11 root')
            attributes = WindowAttributes(background_pixel=0, override_redirect=1)
            # depth/visual CopyFromParent (0/NULL), class InputOutput (1)
            self.window = create_window(self.display, root(self.display), x, y, width, height, 0, 0, 1, None,
                                        CW_BACK_PIXEL | CW_OVERRIDE_REDIRECT, C.byref(attributes))
            if not self.window:
                raise RuntimeError('XCreateWindow failed')
            blank = bitmap(self.display, self.window, b'\0', 1, 1)
            black = Color()
            self.cursor = pixmap_cursor(self.display, blank, blank, C.byref(black), C.byref(black), 0, 0)
            free_pixmap(self.display, blank)
            if self.cursor:
                define_cursor(self.display, self.window, self.cursor)
            self.gc = create_gc(self.display, self.window, 0, None)
            if not self.gc:
                raise RuntimeError('XCreateGC failed')
            map_raised(self.display, self.window)
            self.segment = Segment()
            self.image = create(self.display, visual(self.display, index), depth(self.display, index), 2, None, C.byref(self.segment), width, height)
            if not self.image:
                raise RuntimeError('XShmCreateImage failed')
            image = self.image.contents
            if not (image.width, image.height, image.bits_per_pixel, image.byte_order) == (width, height, 32, 0):
                raise RuntimeError('unsupported X11 pixel packing')
            if not (image.red_mask, image.green_mask, image.blue_mask) == (16711680, 65280, 255):
                raise RuntimeError('unsupported X11 channel order')
            if not width * 4 <= image.bytes_per_line <= width * 4 + 256:
                raise RuntimeError('invalid X11 output state')
            self.pitch = image.bytes_per_line
            size = self.pitch * height
            self.shmid = shmget(0, size, 0o1000 | 0o600)  # IPC_CREAT, owner access only
            if self.shmid < 0:
                raise RuntimeError(os.strerror(C.get_errno()))
            self.address = shmat(self.shmid, None, 0)
            if self.address in (None, C.c_void_p(-1).value):
                raise RuntimeError(os.strerror(C.get_errno()))
            C.memset(self.address, 0, size)
            self.segment.shmid = self.shmid
            self.segment.shmaddr = self.address
            self.segment.read_only = 1  # the X server only reads our frames
            self.image.contents.data = self.address
            if not attach(self.display, C.byref(self.segment)):
                raise RuntimeError('XShmAttach failed')
            self.attached = True
            self.sync(self.display, 0)
            if self.failed:
                raise RuntimeError('X server rejected the output window or shared memory')
            if self.shmctl(self.shmid, 0, None) != 0:  # IPC_RMID
                raise RuntimeError('cannot mark shared segment for removal')
            self.font = load_font(self.display, b'fixed')
            self.sync(self.display, 0)
            if self.failed or not self.font:
                self.font, self.failed = 0, False  # notices still show colour, without text
            else:
                set_font(self.display, self.gc, self.font)
        except BaseException:
            self.close()
            raise

    def present(self):
        """Copy the frame in `address` to the window and wait until the server has it."""
        if not self.display or not self.image:
            raise RuntimeError('X11 output is closed')
        self.failed = False
        if not self.put(self.display, self.window, self.gc, self.image, 0, 0, 0, 0, self.width, self.height, 0):
            raise RuntimeError('XShmPutImage failed')
        self.sync(self.display, 0)  # the segment may be rewritten only after this
        if self.failed:
            raise RuntimeError('X server rejected the frame')

    def notice(self, lines, *, background=0x202124, accent=0xd93025, foreground=0xe8eaed):
        """Replace the window content with a status screen (ASCII text, core font)."""
        if not self.display or not self.gc:
            raise RuntimeError('X11 output is closed')
        self.failed = False
        self.set_foreground(self.display, self.gc, background)
        self.fill(self.display, self.window, self.gc, 0, 0, self.width, self.height)
        self.set_foreground(self.display, self.gc, accent)
        self.fill(self.display, self.window, self.gc, 0, 0, self.width, 12)
        if self.font:
            self.set_foreground(self.display, self.gc, foreground)
            for row, line in enumerate(lines[:8]):
                text = line.encode('ascii', 'replace')[:120]
                self.draw_string(self.display, self.window, self.gc, 48, 72 + row * 24, text, len(text))
        self.sync(self.display, 0)
        if self.failed:
            raise RuntimeError('X server rejected the status screen')

    def close(self):
        if self.display and self.attached:
            self.detach(self.display, C.byref(self.segment))
            self.sync(self.display, 0)
            self.attached = False
        if self.image:
            destroy = C.CFUNCTYPE(C.c_int, C.POINTER(Image))(self.image.contents.functions[1])
            destroy(self.image)
            self.image = None
        if self.address and self.address != C.c_void_p(-1).value:
            self.shmdt(self.address)
            self.address = None
        if self.shmid >= 0:
            self.shmctl(self.shmid, 0, None)
            self.shmid = -1
        if self.display:
            if self.font:
                self.unload_font(self.display, self.font); self.font = 0
            if self.gc:
                self.free_gc(self.display, self.gc); self.gc = None
            if self.cursor:
                self.free_cursor(self.display, self.cursor); self.cursor = 0
            if self.window:
                self.destroy_window(self.display, self.window); self.window = 0
            self.close_display(self.display)
            self.display = None
        if hasattr(self, 'previous_handler'):
            self.set_error_handler(self.previous_handler)
            del self.previous_handler

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
