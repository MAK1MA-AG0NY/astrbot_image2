# astrbot_plugin_gpt_image2

这是一个 AstrBot 外壳插件，核心不再自己请求 Right Code，而是**直接调用你本地已经验证成功的 `rcode_draw.py`**。

## Behavior

- AstrBot 只负责接收 `/draw`。
- 回复图片时，插件会把引用图交给脚本的 `--image` 参数。
- 真正的请求、下载、保存逻辑都由 `rcode_draw.py` 自己完成。

## Usage

- `/draw 描述词`
- 回复图片后再发 `/draw 描述词`

## Config

### `script_path`
脚本路径，默认使用插件目录下的 `rcode_draw.py`

### `python_executable`
Python 解释器路径。留空时使用当前 AstrBot 的 Python。

### `api_key`
Right Code 的 API Key。也兼容环境变量 `right_code_image2`。

### `model`
脚本参数 `--model`，默认：`gpt-image-2`

### `size`
脚本参数 `--size`

### `output`
脚本参数 `--output`

### `keep_url`
脚本参数 `--keep-url`

### `timeout_seconds`
脚本执行超时。

## Notes

- 这版插件不再维护独立的生图请求逻辑。
- 如果脚本本身可以运行，插件就只是在 AstrBot 里替你调用它。
