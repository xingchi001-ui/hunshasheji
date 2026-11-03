
## MiniMax-M3.1 空 content 根因补充

本次日志显示 `content` 为空而 `reasoning` 有内容，且 `finish_reason` 未在日志中显示。MiniMax 官方 OpenAI 兼容文档说明：`max_tokens` 会同时计入推理 token，值太小时会出现 `finish_reason: length` 和空 `content`；新接入应使用 `max_completion_tokens`。官方还说明 M3.1 支持多模态 `image_url` 输入，推理内容可通过 `reasoning_content` 单独返回。网关模型目录标注 `MiniMax-M3.1` 支持图片输入。

来源：https://platform.minimax.io/docs/api-reference/text-openai-api
来源：https://llm-token.cn/model-pricing/MiniMax-M3.1
