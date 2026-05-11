#!/usr/bin/env python3
"""
Right Code 生图脚本
用法:
  纯文生图:  python3 rcode_draw.py "一只猫在月球上"
  带参考图:  python3 rcode_draw.py "把这只猫画成赛博朋克风格" --image ./cat.jpg
  指定尺寸:  python3 rcode_draw.py "prompt" --size 1024x1024
  指定模型:  python3 rcode_draw.py "prompt" --model gpt-image-2
"""

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.request
import urllib.error


# 从环境变量读取 API Key
API_KEY = os.environ.get("right_code_image2", "")

BASE_URL = "https://www.right.codes/draw/v1/images/generations"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = ""


def image_to_base64(path: str) -> str:
    """读取本地图片，返回 data URI (base64)"""
    mime, _ = mimetypes.guess_type(path)
    if not mime:
        mime = "image/png"
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{data}"


def resolve_image(image_input: str) -> str:
    """把图片参数统一成 API 能接受的格式 (URL 或 data URI)"""
    if not image_input:
        return ""
    if image_input.startswith(("http://", "https://", "data:")):
        return image_input
    # 本地文件
    path = os.path.expanduser(image_input)
    if not os.path.isfile(path):
        print(f"错误: 文件不存在 — {path}", file=sys.stderr)
        sys.exit(1)
    return image_to_base64(path)


def generate(prompt: str, image: str = "", model: str = DEFAULT_MODEL,
             size: str = DEFAULT_SIZE) -> dict:
    """调用 Right Code 生图接口，返回完整 JSON 响应"""
    body = {
        "model": model,
        "prompt": prompt,
        "response_format": "url",
    }
    if size:
        body["size"] = size
    resolved = resolve_image(image)
    if resolved:
        body["image"] = [resolved]

    data = json.dumps(body).encode()
    req = urllib.request.Request(
        BASE_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace")
        print(f"HTTP {e.code}: {err_body}", file=sys.stderr)
        sys.exit(1)


def download(url: str, out_path: str) -> str:
    """下载图片到本地"""
    urllib.request.urlretrieve(url, out_path)
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Right Code 生图工具")
    parser.add_argument("prompt", help="生图提示词")
    parser.add_argument("--image", "-i", default="", help="参考图 (本地路径或 URL)")
    parser.add_argument("--model", "-m", default=DEFAULT_MODEL, help=f"模型 (默认: {DEFAULT_MODEL})")
    parser.add_argument("--size", "-s", default=DEFAULT_SIZE, help=f"尺寸 (默认: {DEFAULT_SIZE})")
    parser.add_argument("--output", "-o", default="", help="输出文件路径 (默认: 自动生成)")
    parser.add_argument("--keep-url", action="store_true", help="只打印图片 URL，不下载")
    args = parser.parse_args()

    if not API_KEY:
        print("错误: 请先在脚本顶部填写 API_KEY", file=sys.stderr)
        sys.exit(1)

    print(f"正在生成: {args.prompt}")
    result = generate(args.prompt, args.image, args.model, args.size)

    # 提取图片 URL
    urls = [item.get("url", "") for item in result.get("data", []) if item.get("url")]
    if not urls:
        print("未返回图片，完整响应:", file=sys.stderr)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    # 打印 token 用量
    usage = result.get("usage", {})
    if usage:
        print(f"Token 用量: input={usage.get('input_tokens', '?')}, "
              f"output={usage.get('output_tokens', '?')}, "
              f"total={usage.get('total_tokens', '?')}")

    if args.keep_url:
        for u in urls:
            print(u)
        return

    # 下载图片
    for idx, url in enumerate(urls):
        if args.output:
            out = args.output if len(urls) == 1 else f"_{idx}.".join(args.output.rsplit(".", 1))
        else:
            ts = time.strftime("%Y%m%d_%H%M%S")
            ext = "png"
            out = os.path.join(os.path.expanduser("~/picture/PY_iamge2"), f"draw_{ts}_{idx}.{ext}") if len(urls) > 1 else os.path.join(os.path.expanduser("~/picture/PY_iamge2"), f"draw_{ts}.{ext}")

        print(f"下载中: {url}")
        path = download(url, out)
        print(f"已保存: {os.path.abspath(path)}")


if __name__ == "__main__":
    main()
