
<div align="center">

# Art-generation-telegram-bot

Create AI Art using Telegram bot. Uses Stable Diffusion WebUI

[Installation](#installation) •
[Configuration](#configuration) •
[Bot launch](#bot-launch) •

</div>

Prompt languages: any.   
Bot response languages available: English, Russian.

## Prerequisites
- [Stable-Diffusion-WebUI](https://github.com/AUTOMATIC1111/stable-diffusion-webui) installed and configured
- Bot token from [Telegram Bot](https://core.telegram.org/bots)

## Installation
```
git clone https://github.com/dvarkless/art-generation-telegram-bot.git
```  
#### Create venv and install libraries   
Linux:   
```
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```     
Windows:   
```
python -m venv venv     
./venv/bin/Activate.ps1
pip install -r requirements.txt
```       
[Activate powershell scripts](https://stackoverflow.com/questions/2035193/how-to-run-a-powershell-script) if necessary

## Configuration   
#### Bot configuration
- Paste bot token into `./info/tg_token.txt`
- You can restrict user access to this bot by either using a whitelist or blacklist of users. 
Write user's nicknames or ids into `whitelist.txt` or `blacklist.txt` and choose an appropriate restriction method by modifying the config file `./configs/usage_modes.yml`
- You can change bot's settings in the `./configs/usage_modes.yml` file.      

#### How to add a new SD model   
You can do that by simply downloading model's weights into WebUI's folder and modifying bot's config to be able to use this model properly.   
For example, let's install this [cute generating network](https://huggingface.co/dreamlike-art/dreamlike-photoreal-2.0):   
1. [Download](https://huggingface.co/dreamlike-art/dreamlike-photoreal-2.0/tree/main) model's weights   
2. Move it into `./stable-diffusion-webui/models/Stable-diffusion/` in WebUI repo   
3. Open config file `./configs/models.yml` and write the parameters you want to use. 
Just folow the example structure in this file and you'll be fine.   

#### Changing bot's responses
You can change any phrase in the `./configs/dialogs.yml` file.      
Syntax of YAML files are mostly similar to python.   

## Bot launch
1. Run  [Stable-Diffusion-WebUI](https://github.com/AUTOMATIC1111/stable-diffusion-webui) with the `--api` argument:   
```
python launch.py --api
```   
2. Run the bot itself:   
```
python bot.py
```   

## Contributions
Feel free to contribute to this project. I'll be glad to accept your pull requests.

## License
Distributed under the MIT License. See `LICENSE.txt` for more information.
