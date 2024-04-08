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
import requests
from PIL import Image
from hashids import Hashids

import pibooth
from pibooth.pictures.factory import OpenCvPictureFactory

__version__ = '1.0.0'
HASHIDS = Hashids(salt='pibooth-stephan', alphabet=string.ascii_uppercase)
UPLOAD_QUEUE = queue.Queue()
UPLOAD_URL = 'https://slack.com/api/files.upload'


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
    degr = int(abs_value)
    t1 = (abs_value-degr)*60
    minute = int(t1)
    sec = round((t1 - minute) * 60, 5)
    return degr, minute, sec, loc_value


def rational(number):
    """convert a number to rantional
    Keyword arguments: number
    return: tuple like (1, 2), (numerator, denominator)
    """
    f = Fraction(str(number))
    return f.numerator, f.denominator

LAT = 48.1887301
LNG = 16.3797464

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


def worker(token=None, channel=None):
    while True:
        image, filename = UPLOAD_QUEUE.get()

        try:
            buffer = BytesIO()
            image.thumbnail((2000, 2000))
            image.save(buffer, format='JPEG', quality=90, exif=piexif.dump({
                'GPS': GPS_EXIF,
                'Exif': {
                    piexif.ExifIFD.DateTimeOriginal: arrow.utcnow().format('YYYY:MM:DD HH:mm:ss'),
                    piexif.ExifIFD.OffsetTimeOriginal: '00:00',
                },
                '0th': {
                    piexif.ImageIFD.Make: 'Fotobox',
                    piexif.ImageIFD.Model: 'fotobox.privatwolke.at',
                }
            }))
            buffer.seek(0)
            res = requests.post(UPLOAD_URL, files={'file': (filename, buffer)}, headers={
                'Authorization': f'Bearer {token}',
            }, data={
                'channels': channel,
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
        super().__init__(width, height, *images)

    def build(self, rebuild=False) -> Image:
        if self._final:
            return self._final

        if self.width == 800:
            # smaller pic -> generates pictures for animation
            return super().build(rebuild=rebuild)

        upload = not self._final
        upload_filename = HASHIDS.encode(int(time()))

        image: Image = super().build(rebuild=rebuild)

        if upload:
            # queue the image for upload and cache it
            UPLOAD_QUEUE.put((image, upload_filename))

        self._final = image
        return self._final


@pibooth.hookimpl
def pibooth_setup_picture_factory(factory):
    return CustomPictureFactory(factory.width, factory.height, *factory._images)


@pibooth.hookimpl
def pibooth_configure(cfg):
    """Declare the new configuration options"""
    cfg.add_option('Slack', 'bot_token', '', 'Secret used to authenticate to Slack')
    cfg.add_option('Slack', 'channel', '', 'Channel to upload to')


@pibooth.hookimpl
def pibooth_startup(app, cfg):
    threading.Thread(target=worker, daemon=True, kwargs={
        'token': cfg.get('Slack', 'bot_token'),
        'channel': cfg.get('Slack', 'channel'),
    }).start()
