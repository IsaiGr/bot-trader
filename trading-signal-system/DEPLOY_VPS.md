# 🚀 Guía de Despliegue en VPS (Ubuntu/Debian)

Esta guía te explica cómo instalar el **Trading Signal System** en tu Servidor Privado Virtual (VPS).

## 1. Conéctate a tu servidor
Usa SSH para entrar a la consola de tu servidor:
```bash
ssh root@<IP_DE_TU_SERVIDOR>
```

## 2. Instala Docker y Docker Compose
Si el VPS está recién comprado, instala Docker ejecutando este comando oficial:
```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
```

## 3. Descarga el código del bot
Clona este repositorio o sube los archivos al servidor. Si usas git:
```bash
git clone <URL_DE_TU_REPOSITORIO> trading-bot
cd trading-bot
```

*(Si no usas git, puedes subir la carpeta usando FileZilla o el comando `scp`)*

## 4. Configura tus Secretos (.env)
1. Copia el archivo de ejemplo:
   ```bash
   cp .env.example .env
   ```
2. Edita el archivo `.env` con un editor de texto (como `nano`):
   ```bash
   nano .env
   ```
3. Pega tus claves (API de OpenRouter, Telegram Bot Token, etc.).
4. Guarda presionando `Ctrl+O`, `Enter`, y sal con `Ctrl+X`.

## 5. Inicia el Bot 🤖
Ejecuta el orquestador de Docker en modo silencioso (`-d`):
```bash
docker compose up -d --build
```
¡Listo! Docker descargará Python, instalará las dependencias y dejará el bot corriendo 24/7.

## Comandos Útiles

- **Ver los logs del bot en tiempo real:**
  ```bash
  docker compose logs -f
  ```
- **Detener el bot:**
  ```bash
  docker compose down
  ```
- **Ver los trades del simulador:**
  Como configuramos un volumen, el historial se guardará en la carpeta `data/`.
  ```bash
  cat data/paper_trades_history.csv
  ```
