"""Open a phone photo: HEIC (iPhone) included, turned the right way up."""
import io


def open_photo(data):
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)          # phones store "rotate me" separately
    return im.convert("RGB")
