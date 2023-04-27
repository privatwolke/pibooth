"""Plugin to pimp out pibooth as much as possible."""
import queue
import string
import threading
from fractions import Fraction
from io import BytesIO
from time import time, sleep
from traceback import format_exc

import arrow
import piexif
import pyqrcodeng
import requests
from PIL import Image, ImageDraw
from hashids import Hashids

import pibooth
from pibooth import fonts
from pibooth.pictures.factory import OpenCvPictureFactory

__version__ = '1.0.0'
HASHIDS = Hashids(salt='pibooth-stephan', alphabet=string.ascii_uppercase)
UPLOAD_QUEUE = queue.Queue()
UPLOAD_URL = 'https://fotobox.privatwolke.at/upload'


def deg(value, loc):
    """convert decimal coordinates into degrees, munutes and seconds tuple
    Keyword arguments: value is float gps-value, loc is direction list ["S", "N"] or ["W", "E"]
    return: tuple like (25, 13, 48.343 ,'N')
    """
    if value < 0:
        loc_value = loc[0]
    elif value > 0:
        loc_value = loc[1]
    else:
        loc_value = ''
    abs_value = abs(value)
    deg = int(abs_value)
    t1 = (abs_value-deg)*60
    min = int(t1)
    sec = round((t1 - min) * 60, 5)
    return deg, min, sec, loc_value


def rational(number):
    """convert a number to rantional
    Keyword arguments: number
    return: tuple like (1, 2), (numerator, denominator)
    """
    f = Fraction(str(number))
    return f.numerator, f.denominator

LAT = 47.8836288
LNG = 14.1172314

LAT_DEG = deg(LAT, ('S', 'N'))
LNG_DEG = deg(LNG, ('W', 'E'))

EXIF_LAT = tuple(map(rational, LAT_DEG[:3]))
EXIF_LNG = tuple(map(rational, LNG_DEG[:3]))

GPS_EXIF = {
    piexif.GPSIFD.GPSVersionID: (2, 0, 0, 0),
    piexif.GPSIFD.GPSLatitudeRef: LAT_DEG[3],
    piexif.GPSIFD.GPSLatitude: EXIF_LAT,
    piexif.GPSIFD.GPSLongitudeRef: LNG_DEG[3],
    piexif.GPSIFD.GPSLongitude: EXIF_LNG,
    piexif.GPSIFD.GPSMapDatum: 'WGS-84',
}


def worker(token=None):
    while True:
        image, filename = UPLOAD_QUEUE.get()

        try:
            buffer = BytesIO()
            image.thumbnail((2000, 2000))
            image.save(buffer, format='JPEG', quality=90, exif=piexif.dump({
                'GPS': GPS_EXIF,
                'Exif': {
                    piexif.ExifIFD.DateTimeOriginal: arrow.now().format('YYYY:MM:DD HH:mm:ss'),
                },
                '0th': {
                    piexif.ImageIFD.Make: 'Fotobox',
                    piexif.ImageIFD.Model: 'fotobox.privatwolke.at',
                }
            }))
            buffer.seek(0)
            res = requests.post(UPLOAD_URL, files={'picture': (filename, buffer)}, headers={
                'Authorization': token,
            })
            res.raise_for_status()
            print('Uploaded:', filename)
            UPLOAD_QUEUE.task_done()
        except Exception as ex:
            print(f'Exception in upload worker: {ex}')
            print(format_exc())

            # requeue the image
            UPLOAD_QUEUE.put((image, filename))

        # pause a bit
        sleep(5)


class CustomPictureFactory(OpenCvPictureFactory):

    def __init__(self, width, height, *images):
        self.cache = {}
        self.counter = int(time())
        self.names = Image.open('pibooth/pictures/assets/names.png')
        self.names_small = Image.open('pibooth/pictures/assets/names-small.png')
        super().__init__(width, height, *images)

    def build(self, rebuild=False) -> Image:
        if self._final:
            return self._final

        if self.width == 800:
            # smaller pic -> generates pictures for animation
            image = super().build(rebuild=rebuild)
            image.paste(self.names_small, (20, image.height - 280))
            return image

        upload = not self._final
        upload_filename = HASHIDS.encode(int(time()))

        # make image and paste it onto a bigger canvas
        image: Image = super().build(rebuild=rebuild)
        image.paste(self.names, (200, image.height - 680))
        modified_image = Image.new('RGB', (image.width, image.height + 800))
        draw = ImageDraw.Draw(modified_image)
        draw.rectangle((0, 0, modified_image.width, modified_image.height), fill='white')
        modified_image.paste(image, (0, 0))

        # make QR code and paste it onto the canvas
        qr = pyqrcodeng.create(f'https://fotobox.privatwolke.at/{upload_filename}')
        buffer = BytesIO()
        qr.png(buffer, scale=15)
        with Image.open(buffer) as qr_image:
            modified_image.paste(qr_image, (200, image.height + 15))

        # add explanation text
        font = fonts.get_pil_font('fotobox.privatwolke.at', fonts.get_filename('edwin'), image.width - 200, 100)
        font2 = fonts.get_pil_font(upload_filename, fonts.get_filename('monolisa'), image.width - 200, 100)
        draw.text((900, image.height + 170), 'fotobox.privatwolke.at', fill='black', font=font)
        draw.text((900, image.height + 370), f'Code:', fill='black', font=font)
        draw.text((1250, image.height + 350), upload_filename, fill='black', font=font2)

        if upload:
            # queue the image for upload and cache it
            UPLOAD_QUEUE.put((image, upload_filename))

        self._final = modified_image
        return self._final


@pibooth.hookimpl
def pibooth_setup_picture_factory(factory):
    return CustomPictureFactory(factory.width, factory.height, *factory._images)


@pibooth.hookimpl
def pibooth_configure(cfg):
    """Declare the new configuration options"""
    cfg.add_option('Hochzeit', 'upload_secret', '', 'Secret used to authenticate uploads')


@pibooth.hookimpl
def pibooth_startup(app, cfg):
    threading.Thread(target=worker, daemon=True, kwargs={
        'token': cfg.get('Hochzeit', 'upload_secret')
    }).start()
