# astrbot_plugin_gpt_image2

## Usage

- `/draw 描述词` -> 文生图
- 回复一张图片后再发送 `/draw 描述词` -> 图片编辑

## Troubleshooting

- 默认请求超时已提高到 180 秒，适合慢接口或高负载时段。
- 支持失败自动重试 1 次，可在配置中调整 `retry_times`。
