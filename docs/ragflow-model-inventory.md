# RAGFlow model and embedding inventory

Source snapshot: `ragflow/conf/models/*.json` and `ragflow/conf/all_models.json`. This is a source inventory, not a claim that credentials or deployments are currently active.

## Summary

- Provider configuration files: 61
- Curated provider/model entries: 602
- Global normalized model records: 3757
- Curated chat entries: 429
- Curated embedding entries: 70
- Curated reranker entries: 28
- Curated OCR entries: 13

Providers with no static entries below discover models dynamically from their configured endpoint. A model can carry multiple capabilities, so capability counts overlap.

## Embedding model index

- **302.AI:** `jina-embeddings-v3`
- **Astraflow:** `text-embedding-3-large`
- **BaiChuan:** `Baichuan-Text-Embedding`
- **BaiduYiyan:** `embedding-v1`
- **Bedrock:** `amazon.titan-embed-text-v1`
- **Bedrock:** `amazon.titan-embed-text-v2:0`
- **Bedrock:** `cohere.embed-english-v3`
- **Bedrock:** `cohere.embed-multilingual-v3`
- **Bedrock:** `cohere.embed-v4:0`
- **Cohere:** `embed-english-light-v3.0`
- **Cohere:** `embed-english-v3.0`
- **Cohere:** `embed-multilingual-light-v3.0`
- **Cohere:** `embed-multilingual-v3.0`
- **Cohere:** `embed-v4.0`
- **CometAPI:** `text-embedding-3-large`
- **CometAPI:** `text-embedding-3-small`
- **CometAPI:** `text-embedding-ada-002`
- **DeepInfra:** `Qwen/Qwen3-Embedding-4B`
- **Gemini:** `text-embedding-004`
- **GiteeAI:** `BAAI/bge-m3`
- **GiteeAI:** `jina-clip-v2`
- **HuaweiCloud:** `bge-m3`
- **Jiekou.AI:** `text-embedding-3-large`
- **Jina:** `jina-clip-v2`
- **Jina:** `jina-embeddings-v2-base-en`
- **Jina:** `jina-embeddings-v3`
- **Jina:** `jina-embeddings-v4`
- **Jina:** `jina-embeddings-v5-omni-nano`
- **Jina:** `jina-embeddings-v5-omni-small`
- **Jina:** `jina-embeddings-v5-text-nano`
- **Jina:** `jina-embeddings-v5-text-small`
- **Mistral:** `mistral-embed`
- **n1n:** `text-embedding-3-large`
- **n1n:** `text-embedding-3-small`
- **n1n:** `text-embedding-ada-002`
- **NovitaAI:** `baai/bge-m3`
- **NVIDIA:** `nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1`
- **NVIDIA:** `nvidia/nv-embed-v1`
- **NVIDIA:** `nvidia/nv-embed-v1`
- **NVIDIA:** `nvidia/nv-embedqa-e5-v5`
- **NVIDIA:** `nvidia/nv-embedqa-mistral-7b-v2`
- **OpenAI:** `text-embedding-3-large`
- **OpenAI:** `text-embedding-3-small`
- **OpenAI:** `text-embedding-ada-002`
- **Perplexity:** `pplx-embed-v1-0.6b`
- **Perplexity:** `pplx-embed-v1-4b`
- **Replicate:** `ibm-granite/granite-embedding-278m-multilingual:1f76d42a05f120e12272746d5a2d86b525c13420773f795a4cbef9117d8685f1`
- **Replicate:** `replicate/all-mpnet-base-v2:b6b7585c9640cd7a9572c6e129c9549d79c9c31f0d3fdce7baac7c67ca38f305`
- **SILICONFLOW:** `BAAI/bge-m3`
- **SILICONFLOW:** `Qwen/Qwen3-Embedding-0.6B`
- **Tencent Hunyuan:** `hunyuan-embedding`
- **TogetherAI:** `BAAI/bge-base-en-v1.5`
- **TogetherAI:** `BAAI/bge-large-en-v1.5`
- **TogetherAI:** `intfloat/multilingual-e5-large-instruct`
- **Tongyi-Qianwen:** `text-embedding-v3`
- **Tongyi-Qianwen:** `text-embedding-v4`
- **Upstage:** `solar-embedding-1-large-passage`
- **Upstage:** `solar-embedding-1-large-query`
- **VolcEngine:** `doubao-embedding-vision-251215`
- **Voyage AI:** `voyage-3-large`
- **Voyage AI:** `voyage-3.5`
- **Voyage AI:** `voyage-3.5-lite`
- **Voyage AI:** `voyage-4`
- **Voyage AI:** `voyage-4-large`
- **Voyage AI:** `voyage-4-lite`
- **Voyage AI:** `voyage-code-3`
- **Voyage AI:** `voyage-finance-2`
- **Voyage AI:** `voyage-law-2`
- **ZHIPU-AI:** `embedding-2`
- **ZHIPU-AI:** `embedding-3`

## Curated provider catalog

### 302.AI

Source: `302ai.json`

- `kimi-k2.6` — chat, vision
- `gpt-5.5` — chat, vision
- `gpt-5.4` — chat, vision
- `gpt-5.4-mini` — chat, vision
- `gpt-5.4-nano` — chat, vision
- `gpt-5.2-pro` — chat, vision
- `gpt-5.2` — chat, vision
- `gpt-5.1` — chat, vision
- `gpt-5.1-chat-latest` — chat, vision
- `gpt-5` — chat, vision
- `gpt-5-mini` — chat, vision
- `gpt-5-nano` — chat, vision
- `gpt-5-chat-latest` — chat, vision
- `gpt-4.1` — chat, vision
- `gpt-4.1-mini` — chat, vision
- `gpt-4.1-nano` — chat, vision
- `gpt-4.5-preview` — chat
- `gpt-4o-mini` — chat, vision
- `gpt-4o` — chat, vision
- `gpt-3.5-turbo` — chat
- `gpt-3.5-turbo-16k-0613` — chat
- `whisper-v3-turbo` — asr
- `mistral-ocr-latest` — ocr
- `vlm` — doc_parse
- `jina-embeddings-v3` — embedding
- `jina-reranker-v2-base-multilingual` — rerank

### Tongyi-Qianwen

Source: `aliyun.json`

- `qwen-flash` — chat
- `text-embedding-v4` — embedding
- `qwen3-vl-plus` — vision
- `qwen-tts-flash` — tts
- `qwen-asr-flash` — asr
- `fun-asr` — asr
- `text-embedding-v3` — embedding
- `qwen3-rerank` — rerank

### Anthropic

Source: `anthropic.json`

- `claude-opus-4-8` — chat, vision
- `claude-opus-4-7` — chat, vision
- `claude-opus-4-6` — chat, vision
- `claude-opus-4-5-20251101` — chat, vision
- `claude-opus-4-1-20250805` — chat, vision
- `claude-opus-4-20250514` — chat, vision
- `claude-sonnet-4-6` — chat, vision
- `claude-sonnet-4-5-20250929` — chat, vision
- `claude-sonnet-4-20250514` — chat, vision
- `claude-haiku-4-5-20251001` — chat, vision
- `claude-3-7-sonnet-20250219` — chat, vision
- `claude-3-5-sonnet-20241022` — chat, vision
- `claude-3-5-haiku-20241022` — chat, vision

### Astraflow

Source: `astraflow.json`

- `text-embedding-3-large` — embedding
- `bge-reranker-v2-m3` — rerank
- `IndexTeam/IndexTTS-2` — tts
- `claude-opus-4-7` — chat
- `claude-opus-4-6` — chat
- `claude-sonnet-4-5-20250929` — chat
- `claude-haiku-4-5-20251001` — chat
- `gpt-5.4` — chat
- `gpt-5.4-mini` — chat
- `gpt-5.4-nano` — chat
- `gpt-4o-mini` — chat
- `Qwen/Qwen3-Max` — chat
- `Qwen/Qwen3-Coder` — chat
- `Qwen/Qwen3-32B` — chat
- `Qwen/Qwen3-VL-235B-A22B-Instruct` — chat
- `kimi-k2.6` — chat
- `glm-5.1` — chat
- `MiniMax-M2.7` — chat
- `MiniMax-M2` — chat
- `gemini-2.5-pro` — chat
- `gemini-2.5-flash` — chat

### Avian

Source: `avian.json`

- `deepseek/deepseek-v4-pro` — chat
- `deepseek/deepseek-v4-flash` — chat
- `deepseek/deepseek-v3.2` — chat
- `moonshotai/kimi-k2.5` — chat
- `z-ai/glm-5` — chat
- `minimax/minimax-m2.5` — chat

### Azure-OpenAI

Source: `azure-openai.json`

- Dynamic model discovery; no static model IDs in this provider file.

### BaiChuan

Source: `baichuan.json`

- `Baichuan4` — chat
- `Baichuan4-Air` — chat
- `Baichuan4-Turbo` — chat
- `Baichuan-M3` — chat
- `Baichuan-M3-plus` — chat
- `Baichuan-M2-plus` — chat
- `Baichuan-M2` — chat
- `Baichuan3-Turbo` — chat
- `Baichuan3-Turbo-128k` — chat
- `Baichuan2-Turbo` — chat
- `Baichuan-Text-Embedding` — embedding

### BaiduYiyan

Source: `baidu.json`

- `deepseek-v3.2` — chat
- `deepseek-v4-flash` — chat
- `deepseek-v4-pro` — chat
- `qwen3-32b` — chat
- `qwen3-4b` — chat
- `ernie-5.0` — vision
- `embedding-v1` — embedding
- `qwen3-reranker-4b` — rerank
- `paddleocr-vl-0.9b` — ocr

### Bedrock

Source: `bedrock.json`

- `anthropic.claude-3-5-sonnet-20241022-v2:0` — chat
- `anthropic.claude-3-5-haiku-20241022-v1:0` — chat
- `anthropic.claude-3-opus-20240229-v1:0` — chat
- `anthropic.claude-3-sonnet-20240229-v1:0` — chat
- `anthropic.claude-3-haiku-20240307-v1:0` — chat
- `meta.llama3-1-405b-instruct-v1:0` — chat
- `meta.llama3-1-70b-instruct-v1:0` — chat
- `meta.llama3-1-8b-instruct-v1:0` — chat
- `mistral.mistral-large-2407-v1:0` — chat
- `mistral.mixtral-8x7b-instruct-v0:1` — chat
- `amazon.nova-pro-v1:0` — chat
- `amazon.nova-lite-v1:0` — chat
- `amazon.nova-micro-v1:0` — chat
- `cohere.command-r-plus-v1:0` — chat
- `cohere.command-r-v1:0` — chat
- `amazon.titan-embed-text-v2:0` — embedding
- `amazon.titan-embed-text-v1` — embedding
- `cohere.embed-english-v3` — embedding
- `cohere.embed-multilingual-v3` — embedding
- `cohere.embed-v4:0` — embedding

### Cohere

Source: `cohere.json`

- `command-a-plus-05-2026` — chat
- `command-a-03-2025` — chat
- `command-r7b-12-2024` — chat
- `command-a-translate-08-2025` — chat
- `command-a-reasoning-08-2025` — chat
- `command-a-vision-07-2025` — chat
- `command-r-plus-08-2024` — chat
- `command-r-08-2024` — chat
- `rerank-v4.0-pro` — rerank
- `rerank-v4.0-fast` — rerank
- `rerank-v3.5` — rerank
- `rerank-english-v3.0` — rerank
- `rerank-multilingual-v3.0` — rerank
- `embed-v4.0` — embedding
- `embed-english-v3.0` — embedding
- `embed-english-light-v3.0` — embedding
- `embed-multilingual-v3.0` — embedding
- `embed-multilingual-light-v3.0` — embedding
- `cohere-transcribe-03-2026` — asr

### CometAPI

Source: `cometapi.json`

- `gpt-5.5` — chat, vision
- `gpt-5.4-mini` — chat, vision
- `gpt-5` — chat, vision
- `gpt-4o` — chat, vision
- `claude-sonnet-4-6` — chat, vision
- `gemini-3-pro-preview` — chat, vision
- `deepseek-v3.2` — chat
- `qwen3-235b-a22b` — chat
- `text-embedding-3-small` — embedding
- `text-embedding-3-large` — embedding
- `text-embedding-ada-002` — embedding
- `whisper-1` — asr
- `tts-1` — tts

### DeepInfra

Source: `deepinfra.json`

- `deepseek-ai/DeepSeek-V3.2` — chat
- `Qwen/Qwen3-Embedding-4B` — embedding
- `hexgrad/Kokoro-82M` — tts
- `bosonai/HiggsAudioV2.5` — asr

### DeepSeek

Source: `deepseek.json`

- `deepseek-v4-flash` — chat
- `deepseek-v4-pro` — chat

### Fish Audio

Source: `fishaudio.json`

- `s2-pro` — tts
- `s1` — tts
- `transcribe-1` — asr

### FuturMix

Source: `futurmix.json`

- `gpt-5.5` — chat, vision
- `gpt-5.4` — chat, vision
- `gpt-5.4-mini` — chat, vision
- `gpt-5.4-nano` — chat, vision
- `claude-opus-4-7` — chat, vision
- `claude-opus-4-6` — chat, vision
- `claude-sonnet-4-6` — chat, vision
- `claude-haiku-4-5-20251001` — chat, vision
- `gemini-3.1-pro-preview` — chat, vision
- `gemini-2.5-pro` — chat, vision
- `gemini-2.5-flash` — chat, vision
- `gemini-2.5-flash-lite` — chat, vision

### GiteeAI

Source: `gitee.json`

- `qwen3-8b` — chat
- `qwen3-0.6b` — chat
- `glm-4.7-flash` — chat
- `BAAI/bge-reranker-v2-m3` — rerank
- `BAAI/bge-m3` — embedding
- `GOT-OCR2_0` — ocr
- `DeepSeek-OCR-2` — ocr
- `PaddleOCR-VL-1.5` — ocr
- `jina-clip-v2` — embedding
- `HunyuanOCR` — ocr
- `MinerU2.5` — doc_parse

### Gemini

Source: `google.json`

- `gemini-2.5-flash` — chat
- `text-embedding-004` — embedding

### GPUStack

Source: `gpustack.json`

- Dynamic model discovery; no static model IDs in this provider file.

### Groq

Source: `groq.json`

- `llama-3.1-8b-instant` — chat
- `llama-3.3-70b-versatile` — chat
- `openai/gpt-oss-120b` — chat
- `openai/gpt-oss-20b` — chat
- `groq/compound` — chat
- `groq/compound-mini` — chat
- `openai/gpt-oss-20b` — chat
- `meta-llama/llama-4-scout-17b-16e-instruct` — chat
- `qwen/qwen3-32b` — chat
- `canopylabs/orpheus-v1-english` — tts
- `canopylabs/orpheus-arabic-saudi` — tts
- `whisper-large-v3-turbo` — asr
- `whisper-large-v3` — asr

### HuaweiCloud

Source: `huaweicloud.json`

- `deepseek-v4-pro` — chat
- `deepseek-v4-flash` — chat
- `deepseek-v3.2` — chat
- `deepseek-v3.1-terminus` — chat
- `DeepSeek-V3` — chat
- `deepseek-r1-250528` — chat
- `qwen3-235b-a22b` — chat
- `qwen3-32b` — chat
- `qwen3-30b-a3b` — chat
- `kimi-k2.6` — chat
- `longcat-flash-chat` — chat
- `glm-5` — chat
- `glm-5.1` — chat
- `qwen2.5-vl-72b` — chat, vision
- `bge-m3` — embedding
- `bge-reranker-v2-m3` — rerank

### HuggingFace

Source: `huggingface.json`

- `openai/gpt-oss-120b:fastest` — chat

### Tencent Hunyuan

Source: `hunyuan.json`

- `hunyuan-pro` — chat
- `hunyuan-standard` — chat
- `hunyuan-standard-256K` — chat
- `hunyuan-lite` — chat
- `hunyuan-embedding` — embedding

### Jiekou.AI

Source: `jiekouai.json`

- `deepseek-v4-flash` — chat
- `deepseek-v4-pro` — chat
- `zai-org/glm-4.5` — chat
- `zai-org/glm-4.5v` — chat
- `zai-org/glm-4.7` — chat
- `zai-org/glm-4.7-flash` — chat
- `zai-org/glm-5` — chat
- `baai/bge-reranker-v2-m3` — rerank
- `text-embedding-3-large` — embedding

### Jina

Source: `jina.json`

- `jina-vlm` — chat
- `jina-reranker-v3` — rerank
- `jina-reranker-m0` — rerank
- `jina-colbert-v2` — rerank
- `jina-reranker-v2-base-multilingual` — rerank
- `jina-embeddings-v3` — embedding
- `jina-embeddings-v4` — embedding
- `jina-embeddings-v5-text-small` — embedding
- `jina-embeddings-v5-text-nano` — embedding
- `jina-embeddings-v5-omni-small` — embedding
- `jina-embeddings-v5-omni-nano` — embedding
- `jina-clip-v2` — embedding
- `jina-embeddings-v2-base-en` — embedding

### LM-Studio

Source: `lmstudio.json`

- Dynamic model discovery; no static model IDs in this provider file.

### LocalAI

Source: `localai.json`

- Dynamic model discovery; no static model IDs in this provider file.

### LongCat

Source: `longcat.json`

- `LongCat-Flash-Chat` — chat
- `LongCat-Flash-Lite` — chat
- `LongCat-Flash-Thinking-2601` — chat
- `LongCat-Flash-Omni-2603` — chat
- `LongCat-2.0-Preview` — chat

### MinerU.Net

Source: `mineru.json`

- `vlm` — doc_parse
- `MinerU-HTML` — doc_parse

### MinerU

Source: `mineru_local.json`

- Dynamic model discovery; no static model IDs in this provider file.

### MiniMax

Source: `minimax.json`

- `MiniMax-M3` — chat
- `minimax-m2.7` — chat
- `minimax-m2.7-highspeed` — chat
- `minimax-m2.5` — chat
- `minimax-m2.5-highspeed` — chat
- `minimax-m2.1` — chat
- `minimax-m2.1-highspeed` — chat
- `minimax-m2` — chat
- `minimax-m2-her` — chat
- `speech-2.8-hd` — tts

### Mistral

Source: `mistral.json`

- `mistral-large-latest` — chat
- `mistral-medium-latest` — chat
- `mistral-small-latest` — chat
- `ministral-8b-latest` — chat
- `ministral-3b-latest` — chat
- `pixtral-large-latest` — chat, vision
- `codestral-latest` — chat
- `open-mistral-nemo` — chat
- `open-mistral-7b` — chat
- `open-mixtral-8x7b` — chat
- `open-mixtral-8x22b` — chat
- `magistral-medium-latest` — chat
- `magistral-small-latest` — chat
- `mistral-embed` — embedding
- `mistral-ocr-2512` — ocr

### ModelScope

Source: `modelscope.json`

- Dynamic model discovery; no static model IDs in this provider file.

### Moonshot

Source: `moonshot.json`

- `kimi-k2.6` — chat, vision
- `kimi-k2.5` — chat, vision
- `moonshot-v1-8k` — chat, vision
- `moonshot-v1-32k` — chat
- `moonshot-v1-128k` — chat
- `moonshot-v1-8k-vision-preview` — chat, vision
- `moonshot-v1-32k-vision-preview` — chat, vision
- `moonshot-v1-128k-vision-preview` — chat, vision

### n1n

Source: `n1n.json`

- `gpt-4o-mini` — chat, vision
- `gpt-4o` — chat, vision
- `gpt-5.2` — chat, vision
- `claude-sonnet-4-6` — chat, vision
- `deepseek-v3-0324` — chat
- `deepseek-v3-1-250821` — chat
- `deepseek-v3-1-think-250821` — chat
- `kimi-k2-250905` — chat
- `qwen3-coder-plus` — chat
- `text-embedding-3-small` — embedding
- `text-embedding-3-large` — embedding
- `text-embedding-ada-002` — embedding
- `BAAI/bge-reranker-v2-m3` — rerank
- `Qwen/Qwen3-Reranker-0.6B` — rerank

### NovitaAI

Source: `novita.json`

- `deepseek/deepseek-v4-pro` — chat
- `meta-llama/llama-3.3-70b-instruct` — chat
- `qwen/qwen3-30b-a3b-fp8` — chat
- `qwen/qwen3-235b-a22b-fp8` — chat
- `moonshotai/kimi-k2-instruct` — chat
- `google/gemma-3-27b-it` — chat
- `mistralai/mistral-nemo` — chat
- `baai/bge-m3` — embedding
- `baai/bge-reranker-v2-m3` — rerank

### NVIDIA

Source: `nvidia.json`

- `abacusai/dracarys-llama-3.1-70b-instruct` — chat
- `bytedance/seed-oss-36b-instruct` — chat
- `deepseek-ai/deepseek-v4-flash` — chat
- `deepseek-ai/deepseek-v4-pro` — chat
- `nvidia/nv-embed-v1` — embedding
- `google/codegemma-7b` — chat
- `google/gemma-2-2b-it` — chat
- `google/gemma-4-31b-it` — chat
- `meta/llama-3.2-90b-vision-instruct` — chat, vision
- `meta/llama-4-maverick-17b-128e-instruct` — chat
- `minimaxai/minimax-m2.5` — chat
- `minimaxai/minimax-m2.7` — chat
- `mistralai/mistral-7b-instruct-v0.3` — chat
- `mistralai/mistral-large-3-675b-instruct-2512` — chat
- `mistralai/mistral-medium-3.5-128b` — chat, vision
- `mistralai/mistral-nemotron` — chat
- `moonshotai/kimi-k2.6` — chat, vision
- `moonshotai/kimi-k2-instruct` — chat
- `moonshotai/kimi-k2-thinking` — chat
- `nvidia/gliner-pii` — chat
- `nvidia/llama-3.1-nemoguard-8b-content-safety` — chat
- `nvidia/llama-3.1-nemoguard-8b-topic-control` — chat
- `nvidia/llama-3.1-nemotron-nano-8b-v1` — chat
- `nvidia/llama-3.1-nemotron-safety-guard-8b-v3` — chat
- `nvidia/llama-3.1-nemotron-ultra-253b-v1` — chat
- `nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1` — embedding
- `nvidia/llama-3.3-nemotron-super-49b-v1` — chat
- `nvidia/llama-3.3-nemotron-super-49b-v1.5` — chat
- `nvidia/nemotron-3-nano-30b-a3b` — chat
- `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` — chat, vision
- `nvidia/nemotron-3-super-120b-a12b` — chat
- `nvidia/nemotron-content-safety-reasoning-4b` — chat
- `nvidia/nemotron-mini-4b-instruct` — chat
- `nvidia/nv-embed-v1` — embedding
- `nvidia/nv-embedqa-e5-v5` — embedding
- `nvidia/nv-embedqa-mistral-7b-v2` — embedding
- `nvidia/nv-rerankqa-mistral-4b-v3` — rerank
- `nvidia/llama-3.2-nv-rerankqa-1b-v2` — rerank
- `nvidia/nvidia-nemotron-nano-9b-v2` — chat
- `nvidia/riva-translate-4b-instruct-v1.1` — chat
- `openai/gpt-oss-120b` — chat
- `qwen/qwen3.5-122b-a10b` — chat
- `qwen/qwen3-coder-480b-a35b-instruct` — chat
- `z-ai/glm5` — chat
- `z-ai/glm-5.1` — chat
- `z-ai/glm4.7` — chat

### Ollama

Source: `ollama.json`

- Dynamic model discovery; no static model IDs in this provider file.

### OpenAI

Source: `openai.json`

- `gpt-5.5` — chat, vision
- `gpt-5.4` — chat, vision
- `gpt-5.4-mini` — chat, vision
- `gpt-5.4-nano` — chat, vision
- `gpt-5.2-pro` — chat, vision
- `gpt-5.2` — chat, vision
- `gpt-5.1` — chat, vision
- `gpt-5.1-chat-latest` — chat, vision
- `gpt-5` — chat, vision
- `gpt-5-mini` — chat, vision
- `gpt-5-nano` — chat, vision
- `gpt-5-chat-latest` — chat, vision
- `gpt-4.1` — chat, vision
- `gpt-4.1-mini` — chat, vision
- `gpt-4.1-nano` — chat, vision
- `gpt-4.5-preview` — chat
- `gpt-4o-mini` — chat, vision
- `gpt-4o` — chat, vision
- `gpt-3.5-turbo` — chat
- `gpt-3.5-turbo-16k-0613` — chat
- `text-embedding-ada-002` — embedding
- `text-embedding-3-small` — embedding
- `text-embedding-3-large` — embedding
- `whisper-1` — asr
- `gpt-4` — chat
- `gpt-4-turbo` — chat
- `gpt-4-32k` — chat
- `tts-1` — tts

### OpenRouter

Source: `openrouter.json`

- `google/gemma-4-31b-it` — chat
- `minimax/minimax-m2.5` — chat
- `tencent/hy3-preview` — chat
- `openai/gpt-audio-mini` — tts
- `openai/whisper-large-v3` — asr

### OrcaRouter

Source: `orcarouter.json`

- `orcarouter/auto` — chat
- `openai/tts-1` — tts

### PaddleOCR.Net

Source: `paddleocr.json`

- `PaddleOCR-VL-1.6` — ocr
- `PaddleOCR-VL-1.5` — ocr
- `PP-OCRv6` — ocr
- `PP-OCRv5` — ocr
- `PP-StructureV3` — ocr

### PaddleOCR

Source: `paddleocr_local.json`

- Dynamic model discovery; no static model IDs in this provider file.

### Perplexity

Source: `perplexity.json`

- `sonar` — chat
- `sonar-pro` — chat
- `sonar-reasoning-pro` — chat
- `sonar-deep-research` — chat
- `pplx-embed-v1-0.6b` — embedding
- `pplx-embed-v1-4b` — embedding

### PPIO

Source: `ppio.json`

- `deepseek/deepseek-v4-flash` — chat
- `deepseek/deepseek-v4-pro` — chat
- `deepseek/deepseek-r1/community` — chat
- `deepseek/deepseek-v3/community` — chat
- `deepseek/deepseek-r1` — chat
- `deepseek/deepseek-v3` — chat
- `deepseek/deepseek-r1-distill-llama-70b` — chat
- `deepseek/deepseek-r1-distill-qwen-32b` — chat
- `deepseek/deepseek-r1-distill-qwen-14b` — chat
- `deepseek/deepseek-r1-distill-llama-8b` — chat
- `qwen/qwen-2.5-72b-instruct` — chat
- `qwen/qwen-2-vl-72b-instruct` — chat
- `meta-llama/llama-3.2-3b-instruct` — chat
- `qwen/qwen2.5-32b-instruct` — chat
- `baichuan/baichuan2-13b-chat` — chat
- `meta-llama/llama-3.1-70b-instruct` — chat
- `meta-llama/llama-3.1-8b-instruct` — chat
- `01-ai/yi-1.5-34b-chat` — chat
- `01-ai/yi-1.5-9b-chat` — chat
- `thudm/glm-4-9b-chat` — chat
- `qwen/qwen-2-7b-instruct` — chat

### Qiniu

Source: `qiniu.json`

- `deepseek/deepseek-v4-flash` — chat
- `deepseek/deepseek-v4-pro` — chat
- `moonshotai/kimi-k2.6` — vision
- `moonshotai/kimi-k2.5` — vision
- `z-ai/glm-5.1` — chat
- `z-ai/glm-5` — chat
- `minimax/minimax-m2.7` — chat
- `minimax/minimax-m2.5` — chat
- `minimax/minimax-m2.5-highspeed` — chat
- `minimax/minimax-m2.1` — chat
- `kimi-k2-thinking` — chat
- `meituan/longcat-flash-lite` — chat
- `qwen3-max` — chat
- `z-ai/glm-4.6` — chat
- `z-ai/glm-4.7` — chat
- `deepseek/deepseek-v3.2-251201` — chat
- `deepseek/deepseek-v3.2-exp-thinking` — chat
- `deepseek/deepseek-v3.1-terminus` — chat
- `deepseek/deepseek-v3.1-terminus-thinking` — chat
- `deepseek-v3.1` — chat
- `deepseek-v3-0324` — chat
- `deepseek-r1-0528` — chat
- `deepseek-r1` — chat
- `doubao-seed-1.6-flash` — vision
- `doubao-1.5-pro-32k` — vision
- `doubao-seed-1.6` — vision
- `doubao-seed-2.0-pro` — vision
- `doubao-seed-2.0-lite` — chat
- `doubao-seed-2.0-mini` — vision
- `doubao-seed-2.0-code` — chat
- `qwen3-next-80b-a3b-thinking` — chat
- `qwen3-235b-a22b-thinking-2507` — chat
- `qwen3-max-2026-01-23` — chat
- `qwen3-next-80b-a3b-instruct` — chat
- `qwen3-max-preview` — chat
- `qwen-2.5-vl-72b-instruct` — vision
- `qwen3-coder-480b-a35b-instruct` — chat
- `qwen-turbo` — chat
- `qwen3-235b-a22b-instruct-2507` — chat
- `qwen3-32b` — chat
- `qwen3-30b-a3b` — chat
- `qwen3-235b-a22b` — chat
- `qwen-2.5-vl-7b-instruct` — vision
- `qwen-vl-max-2025-01-25` — vision
- `qwen2.5-max-2025-01-25` — chat
- `minimax-m1` — chat
- `glm-4.5` — chat
- `qwen3-vl-30b-a3b-instruct` — vision
- `deepseek-v3` — chat
- `qwen3-30b-a3b-thinking-2507` — chat
- `glm-4.5-air` — chat
- `qwen3.5-397b-a17b` — vision
- `qwen/qwen3.5-plus` — vision
- `qwen/qwen3.6-plus` — chat
- `deepseek/deepseek-v3.2-exp` — chat
- `qwen/qwen3.7-max` — chat
- `qwen/qwen3.6-27b` — vision
- `tencent/hy3-preview` — chat
- `qwen3.5-35b-a3b` — vision
- `qwen3-vl-30b-a3b-thinking` — vision
- `qwen3-30b-a3b-instruct-2507` — chat

### RAGcon

Source: `ragcon.json`

- Dynamic model discovery; no static model IDs in this provider file.

### Replicate

Source: `replicate.json`

- `meta/llama-4-maverick-instruct` — chat
- `meta/llama-4-scout-instruct` — chat
- `meta/meta-llama-3-70b-instruct` — chat
- `meta/meta-llama-3-8b-instruct` — chat
- `replicate/all-mpnet-base-v2:b6b7585c9640cd7a9572c6e129c9549d79c9c31f0d3fdce7baac7c67ca38f305` — embedding
- `ibm-granite/granite-embedding-278m-multilingual:1f76d42a05f120e12272746d5a2d86b525c13420773f795a4cbef9117d8685f1` — embedding

### SILICONFLOW

Source: `siliconflow.json`

- `Pro/deepseek-ai/DeepSeek-V4-Pro` — chat
- `Pro/deepseek-ai/DeepSeek-V4-Flash` — chat
- `Pro/moonshotai/Kimi-K2.6` — chat, vision
- `Pro/zai-org/GLM-5.1` — chat
- `qwen/qwen3-8b` — chat
- `qwen/qwen3.5-4b` — chat
- `tencent/hunyuan-mt-7b` — chat
- `BAAI/bge-reranker-v2-m3` — rerank
- `Qwen/Qwen3-Embedding-0.6B` — embedding
- `BAAI/bge-m3` — embedding
- `fnlp/MOSS-TTSD-v0.5` — tts
- `FunAudioLLM/CosyVoice2-0.5B` — tts
- `FunAudioLLM/SenseVoiceSmall` — asr

### StepFun

Source: `stepfun.json`

- `step-3.5-flash` — chat
- `step-3.5-flash-paid` — chat
- `step-2-16k` — chat
- `step-1-256k` — chat
- `step-1-128k` — chat
- `step-1-32k` — chat
- `step-1-8k` — chat
- `step-1-flash` — chat
- `step-1v-32k` — chat, vision
- `step-1v-8k` — chat, vision
- `step-1o-vision-32k` — chat, vision
- `step-tts-2` — tts
- `stepaudio-2.5-tts` — tts
- `step-tts-mini` — tts

### TogetherAI

Source: `togetherai.json`

- `openai/gpt-oss-20b` — chat
- `meta-llama/Llama-3.3-70B-Instruct-Turbo` — chat
- `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8` — chat
- `intfloat/multilingual-e5-large-instruct` — embedding
- `BAAI/bge-large-en-v1.5` — embedding
- `BAAI/bge-base-en-v1.5` — embedding
- `mixedbread-ai/mxbai-rerank-large-v2` — rerank
- `openai/whisper-large-v3` — asr
- `canopylabs/orpheus-3b-0.1-ft` — tts

### TokenHub

Source: `tokenhub.json`

- `gpt-4o-mini` — chat, vision
- `gpt-4o` — chat, vision
- `gpt-4` — chat
- `gpt-4-turbo` — chat
- `claude-3-5-sonnet` — chat, vision
- `gemini-1.5-pro` — chat
- `gemini-1.5-flash` — chat

### TokenPony

Source: `tokenpony.json`

- `qwen3-8b` — chat
- `deepseek-v3-0324` — chat
- `qwen3-32b` — chat
- `kimi-k2-instruct-0905` — chat
- `deepseek-r1-0528` — chat
- `qwen3-coder-480b` — chat
- `hunyuan-a13b-instruct` — chat
- `qwen3-next-80b-a3b-instruct` — chat
- `deepseek-v3.2-exp` — chat
- `deepseek-v3.1-terminus` — chat
- `qwen3-vl-235b-a22b-instruct` — chat
- `qwen3-vl-30b-a3b-instruct` — chat
- `deepseek-ocr` — chat
- `qwen3-235b-a22b-instruct-2507` — chat
- `glm-4.6` — chat
- `minimax-m2` — chat

### Upstage

Source: `upstage.json`

- `solar-pro3` — chat
- `solar-pro2` — chat
- `solar-pro` — chat
- `solar-mini` — chat
- `solar-embedding-1-large-query` — embedding
- `solar-embedding-1-large-passage` — embedding

### VLLM

Source: `vllm.json`

- Dynamic model discovery; no static model IDs in this provider file.

### VolcEngine

Source: `volcengine.json`

- `doubao-seed-2-0-pro-260215` — chat
- `doubao-embedding-vision-251215` — embedding

### Voyage AI

Source: `voyage.json`

- `voyage-4-large` — embedding
- `voyage-4` — embedding
- `voyage-4-lite` — embedding
- `voyage-3.5` — embedding
- `voyage-3.5-lite` — embedding
- `voyage-3-large` — embedding
- `voyage-code-3` — embedding
- `voyage-law-2` — embedding
- `voyage-finance-2` — embedding
- `rerank-2.5` — rerank
- `rerank-2.5-lite` — rerank
- `rerank-2` — rerank
- `rerank-2-lite` — rerank

### xAI

Source: `xai.json`

- `grok-4` — chat
- `grok-3` — chat
- `grok-3-fast` — chat
- `grok-3-mini` — chat
- `grok-3-mini-mini-fast` — chat
- `grok-2-vision` — vision
- `eve` — tts

### Xiaomi

Source: `xiaomi.json`

- `mimo-v2.5-pro` — chat
- `mimo-v2.5` — chat
- `mimo-v2.5-asr` — asr
- `mimo-v2.5-tts` — tts
- `mimo-v2-tts` — tts

### Xinference

Source: `xinference.json`

- Dynamic model discovery; no static model IDs in this provider file.

### XunFei Spark

Source: `xunfei.json`

- `spark-x` — chat

### ZHIPU-AI

Source: `zhipu-ai.json`

- `glm-5` — chat
- `glm-5-turbo` — chat
- `glm-5v-turbo` — chat
- `glm-4.7` — chat
- `glm-4.7-flashx` — chat
- `glm-4.6` — chat
- `glm-4.6v-Flash` — chat, vision
- `glm-4.5` — chat
- `glm-4.5-x` — chat
- `glm-4.5-air` — chat
- `glm-4.5-airx` — chat
- `glm-4.5-flash` — chat
- `glm-4.5v` — vision
- `glm-4-plus` — chat
- `glm-4-0520` — chat
- `glm-4` — chat
- `glm-4-airx` — chat
- `glm-4-air` — chat
- `glm-4-flash` — chat
- `glm-4-flashx` — chat
- `glm-4-long` — chat
- `glm-4v` — vision
- `glm-4-9b` — chat
- `embedding-2` — embedding
- `embedding-3` — embedding
- `glm-asr-2512` — asr
- `glm-tts` — tts
- `glm-ocr` — ocr
- `rerank` — rerank


