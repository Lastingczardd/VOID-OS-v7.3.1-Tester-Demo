@echo off
where ollama >nul 2>&1 || (echo Install Ollama first from ollama.com& pause& exit /b 1)
echo Installing chat model...
ollama pull qwen2.5:3b
echo Installing coding model...
ollama pull qwen2.5-coder:7b
echo Optional vision model:
echo   ollama pull gemma3:4b
pause
