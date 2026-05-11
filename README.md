# astrbot_plugin_gpt_image2

一个面向 AstrBot + NapCat/OneBot v11 的 gpt-image-2 画图插件，支持文生图与“回复图片后编辑”两种模式。

## Features

- **文生图**：直接发送 `/draw 描述词` 即可生成图片。
- **图文分流**：如果你的消息是“回复了一张图片”再发送 `/draw 描述词`，插件会自动改走 `chat/completions`，让图片和文字一起进入上游模型。
- **自动结果处理**：兼容 `b64_json` 与 `url` 两种返回形式；优先用 `url`，否则落盘 base64。
- **更稳的错误输出**：会把上游响应状态码、URL、Content-Type 和响应体片段打印到日志里，方便排障。
- **失败重试**：网络抖动或偶发超时时可自动重试。

## Advantages

- 适合 QQ + NapCat + AstrBot 的常见部署组合。
- 代码路径简单，便于替换不同供应商的图片接口。
- 对上游服务的错误可观测性较强，能快速判断是权限、路由、超时还是服务端故障。

## Usage

- `/draw 描述词` → 文生图
- 回复一张图片后再发送 `/draw 描述词` → 图文联合出图（chat/completions）

## Safety & Anti-mistake Mechanisms

- **自动识别回复图**：不会把“引用图片”误当成普通文本。
- **超时保护**：默认请求超时已提高到 180 秒，适合慢接口或高负载场景。
- **自动重试**：默认失败重试 1 次，可根据供应商稳定性调整。
- **可读错误日志**：保留上游返回内容片段，避免只看到 `raise_for_status()`。
- **支持代理**：方便在网络不稳定或需要代理转发时使用。

## AstrBot Config

插件支持以下可配置项：

### `base_url`
基础地址，例如：`https://right.codes/gpt`

### `image_api_url`
图片生成接口地址。留空时自动拼接为：`{base_url}/v1/images/generations`

### `chat_api_url`
图文聊天接口地址。留空时自动拼接为：`{base_url}/v1/chat/completions`

### `image_edit_api_url`
图片编辑接口地址。保留作兼容项，当前插件默认不直接调用。

### `api_key`
你的供应商 API Key。

### `model`
模型名，默认：`gpt-image-2`

### `size`
图片尺寸，例如：`1024x1024`

### `response_format`
返回格式，建议：`url`

### `auth_header`
鉴权头，二选一：
- `Authorization`
- `x-api-key`

### `timeout_seconds`
请求超时时间（秒），默认：`180`

### `retry_times`
失败重试次数，默认：`1`

### `proxy`
可选代理地址，例如：`http://127.0.0.1:7890`

## Troubleshooting

- 如果日志里出现 `403`，通常是 token 没有模型权限。
- 如果出现 `503`，通常是供应商侧没有可用渠道或模型。
- 如果出现 `502 / 524`，通常是上游服务超时或网关故障。
- 如果生成成功但编辑不生效，优先检查回复消息里是否真有图片引用，以及 `image` 参数是否被供应商正确接受。

## Notes

- 插件依赖 `httpx`。
- 推荐将插件目录直接作为 AstrBot 插件安装，或打包为带顶层文件夹的 zip。
