"""표시 전용 SVG/PNG. NN·깊이 추정·지도/경로 계산을 다시 수행하지 않는다."""
from html import escape
import struct
import zlib

from snapshot import SnapshotError,DISPLAY_CROPS


COLORS={'GT':'#b08aff','ESTIMATE':'#5eb7ff','UNCERTAINTY':'#ffca6c','PATH':'#63e6b7'}


def map_svg(body,*,show_gt=False):
    layers=[x for x in body.get('map',{}).get('layers',[]) if show_gt or x['kind']!='GT']
    frames={x['frame'] for x in layers}
    if len(frames)>1:raise SnapshotError('MAP_FRAME_MISMATCH: 서로 다른 frame을 겹치지 않습니다')
    points=[p for x in layers for p in x['points']]
    if not points:return None
    x0=min(p[0] for p in points);x1=max(p[0] for p in points);y0=min(p[1] for p in points);y1=max(p[1] for p in points)
    width=max(x1-x0,.1);height=max(y1-y0,.1);scale=min(720/width,420/height)
    def coord(p):return f'{40+(p[0]-x0)*scale:.3f},{460-(p[1]-y0)*scale:.3f}'
    svg=['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 500">',
         '<rect width="800" height="500" fill="#111827"/>']
    for layer in layers:
        kind=layer['kind'];closed=bool(layer.get('closed'));tag='polygon' if closed else 'polyline'
        fill=COLORS[kind] if kind=='UNCERTAINTY' and closed else 'none'
        opacity='.18' if fill!='none' else '1'
        dashed=' stroke-dasharray="7 5"' if kind=='GT' else ''
        svg.append(f'<{tag} points="{" ".join(coord(p) for p in layer["points"])}" stroke="{COLORS[kind]}" '
                   f'fill="{fill}" fill-opacity="{opacity}" stroke-width="{3 if kind=="PATH" else 2}"{dashed}/>')
    svg.append(f'<text x="20" y="25" fill="#cbd5e1" font-family="sans-serif" font-size="14">'
               f'{escape(next(iter(frames)))} · m · GT는 표시 전용</text></svg>')
    return ''.join(svg)


def _png(width,height,rows):
    def chunk(tag,raw):return struct.pack('>I',len(raw))+tag+raw+struct.pack('>I',zlib.crc32(tag+raw)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(rows,3))+chunk(b'IEND',b'')


def encoded_dimensions(raw,encoding):
    if encoding=='PNG':
        if len(raw)<33 or raw[:8]!=b'\x89PNG\r\n\x1a\n' or raw[12:16]!=b'IHDR':raise SnapshotError('INVALID_PNG_HEADER')
        return struct.unpack('>II',raw[16:24])
    if raw[:2]!=b'\xff\xd8':raise SnapshotError('INVALID_JPEG_HEADER')
    at=2
    while at+4<=len(raw):
        if raw[at]!=255:raise SnapshotError('INVALID_JPEG_MARKER')
        while at<len(raw) and raw[at]==255:at+=1
        if at>=len(raw):break
        marker=raw[at];at+=1
        if marker in (0xd8,0xd9,0x01) or 0xd0<=marker<=0xd7:continue
        if at+2>len(raw):break
        size=int.from_bytes(raw[at:at+2],'big')
        if size<2 or at+size>len(raw):break
        if marker in (0xc0,0xc1,0xc2):
            if size<8:break
            height,width=struct.unpack('>HH',raw[at+3:at+7]);return width,height
        if marker==0xda:break
        at+=size
    raise SnapshotError('JPEG_DIMENSIONS_UNAVAILABLE')


def preview(media,max_width=320):
    """검산된 payload만 소비한다. RAW16의0은 검정 UNKNOWN이며 FREE가 아니다."""
    d=media.descriptor;raw=media.raw;width=d['width'];height=d['height'];encoding=d['encoding']
    if encoding in ('PNG','JPEG'):
        if encoded_dimensions(raw,encoding)!=(width,height):raise SnapshotError('ENCODED_MEDIA_DIMENSIONS_DIFFER')
        role=d.get('display_crop')
        if role is not None:
            if (type(role) is not str or role not in DISPLAY_CROPS or DISPLAY_CROPS[role][0]!=d.get('panel') or
                    (encoding,width,height)!=('PNG',1280,720)):raise SnapshotError('INVALID_DISPLAY_CROP')
            # Only the two left quadrants are displayable. Right-bottom GT is
            # never included; the original bytes/SHA remain in Media unchanged.
            from io import BytesIO
            from PIL import Image
            with Image.open(BytesIO(raw)) as image:
                if image.format!='PNG' or image.size!=(1280,720):raise SnapshotError('INVALID_DISPLAY_CROP_PNG')
                image.load();cropped=image.crop(DISPLAY_CROPS[role][1]);output=BytesIO()
                cropped.save(output,format='PNG');return output.getvalue()
        return raw
    stride=max(1,(width+max_width-1)//max_width);xs=range(0,width,stride);ys=range(0,height,stride)
    rows=bytearray()
    for y in ys:
        rows.append(0)
        for x in xs:
            if encoding=='NV12':
                yy=raw[y*width+x];uv=width*height+(y//2)*width+(x//2)*2
                u=raw[uv]-128;v=raw[uv+1]-128;c=max(0,yy-16)
                values=((298*c+409*v+128)>>8,(298*c-100*u-208*v+128)>>8,(298*c+516*u+128)>>8)
                rows.extend(max(0,min(255,t)) for t in values)
            else:
                value=int.from_bytes(raw[2*(y*width+x):2*(y*width+x)+2],'little')
                maximum=3000 if d.get('depth_unit')=='mm' else 65535
                if not value:rows.extend((0,0,0))
                else:
                    f=min(1,value/maximum);rows.extend((int(255*(1-f)),int(180*(1-abs(2*f-1))),int(255*f)))
    return _png(len(xs),len(ys),bytes(rows))
