import asyncio
import base64
import io
import logging
from difflib import get_close_matches
from pathlib import Path
from typing import List

import requests
import yaml
from PIL import Image, PngImagePlugin

from setup_handler import get_handler

URL = "http://127.0.0.1:7860"


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(
                Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


class StableDiffusionAccess(metaclass=Singleton):
    def __init__(self,
                 api_url="http://127.0.0.1:7860",
                 temp_dir='./temp/',
                 model_config_obj=None):
        self.model = ''
        self.temp_dir = Path(temp_dir)
        self.api_url = api_url
        if model_config_obj:
            self.model_config = model_config_obj
        else:
            raise KeyError('Please provide model_config class')

        self.logger = logging.getLogger(__name__)
        self.logger.addHandler(get_handler())
        self.logger.setLevel(logging.DEBUG)

    def is_connected(self):
        self.logger.debug('Call: is_connected')
        r = requests.get(url=f"{self.api_url}/user")
        if r.status_code != 200:
            return False
        return True

    def change_model(self, model_name):
        self.logger.debug('Call: change_model')
        params_dict = self.get_model_params(model_name)
        model_checkpoints = self.get_sd_models()
        my_checkpoint = get_close_matches(params_dict['checkpoint'],
                                          model_checkpoints, n=1)[0]
        self._set_model(my_checkpoint)
        self.model = model_name

    def get_sd_models(self):
        self.logger.debug('Call: get_sd_models')
        response = requests.get(url=f'{self.api_url}/sdapi/v1/sd-models')
        response = response.json()
        print(response)
        return [x['title'] for x in response]

    def get_model_params(self, model_name, specific='txt2img') -> dict:
        self.logger.debug('Call: get_model_params')

        assert model_name in self.model_config['available_models']
        out_dict = self.model_config[model_name]
        out_dict.update(self.model_config[model_name]['default_params'])
        if specific == 'img2img':
            out_dict.update(self.model_config[model_name]['img2img_params'])
        return out_dict

    def get_image_repr(self, img_path: Path):
        self.logger.debug('Call: get_image_repr')
        buffered = io.BytesIO()
        image = Image.open(img_path)
        image.save(buffered, format="PNG")
        img_base64 = 'data:image/png;base64,' + \
            str(base64.b64encode(buffered.getvalue()), 'utf-8')
        return img_base64

    def _set_model(self, model_chk):
        options = {
            'sd_model_checkpoint': model_chk,
        }
        requests.post(url=f'{self.api_url}/sdapi/v1/options', json=options)

    def _pack_images(self, response, file_prefix) -> list[Path]:
        r = response.json()
        paths_list = []
        for i, img_bytes in enumerate(r['images']):
            image = Image.open(io.BytesIO(
                base64.b64decode(img_bytes.split(",", 1)[0])))

            png_payload = {
                "image": "data:image/png;base64," + img_bytes
            }
            response2 = requests.post(
                url=f'{self.api_url}/sdapi/v1/png-info', json=png_payload)

            pnginfo = PngImagePlugin.PngInfo()
            pnginfo.add_text("parameters", response2.json().get("info"))
            filename = f'{file_prefix}_txt2img_gen_{i}.png'
            file_path = self.temp_dir / filename
            image.save(file_path, pnginfo=pnginfo)
            paths_list.append(file_path)

        return paths_list

    async def txt2img(self, prompt: str, model_name: str,
                      image_size: str, file_prefix='') -> list[Path]:
        self.logger.debug('Call: txt2img')
        if self.model != model_name:
            self.change_model(model_name)

        model_payload = self.get_model_params(model_name)
        img_w, img_h = [int(s) for s in image_size.split('x')]
        payload = {
            "prompt": prompt,
            "width": img_w,
            "height": img_h,

            "do_not_save_samples": True,
            "n_iter": 4,
        }
        payload |= model_payload

        response = requests.post(url=f'{URL}/sdapi/v1/txt2img', json=payload)
        file_prefix = file_prefix + '_' + '_'.join(prompt.split()[:5])

        return self._pack_images(response, file_prefix)

    async def img2img(self, prompt: str, model_name: str, image_size: str,
                      img_path: str | Path, file_prefix='') -> list[Path]:
        self.logger.debug('Call: img2img')
        img_repr = self.get_image_repr(Path(img_path))

        if self.model != model_name:
            self.change_model(model_name)

        model_payload = self.get_model_params(model_name, specific='img2img')
        img_w, img_h = [int(s) for s in image_size.split('x')]
        payload = {
            "init_images": [img_repr],
            "prompt": prompt,
            "width": img_w,
            "height": img_h,

            "do_not_save_samples": True,
            "n_iter": 4,
        }
        payload |= model_payload

        response = requests.post(
            url=f'{self.api_url}/sdapi/v1/img2img', json=payload)
        file_prefix = file_prefix + '_' + '_'.join(prompt.split()[:5])
        Path(img_path).unlink()

        return self._pack_images(response, file_prefix)

    async def upscale_img(self) -> List[str]:
        self.logger.debug('Call: upscale_img')
        pass
