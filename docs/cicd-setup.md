# CI/CD de educadata (GitHub Actions, runner self-hosted)

Flujo: push a `main` -> el runner en el servidor hace `git pull`, instala
`requirements.txt` en `/tacopy/educadata/.venv` y reinicia el servicio `educadata`
(gunicorn en 10.3.29.160:8700). `collectstatic` y `migrate` se corren a mano.

Workflow: `.github/workflows/deploy.yml`

## 1. Crear el runner propio de educadata

Como `azael.zarate` en el servidor (el tar ya está en /tacopy/):

```bash
mkdir -p /tacopy/actions-runner-educadata
cd /tacopy/actions-runner-educadata
tar xzf /tacopy/actions-runner-linux-x64-2.337.0.tar.gz
```

Token: GitHub -> azaelcupa/educadata -> Settings -> Actions -> Runners ->
New self-hosted runner (Linux x64). Copiar el token (caduca en 1 hora).

```bash
./config.sh --url https://github.com/azaelcupa/educadata \
  --token <TOKEN> \
  --name tacopy-educadata \
  --labels educadata \
  --work _work \
  --unattended
```

Instalarlo como servicio para que arranque solo:

```bash
sudo ./svc.sh install azael.zarate
sudo ./svc.sh start
sudo ./svc.sh status
```

## 2. Permiso para reiniciar gunicorn sin contraseña

```bash
sudo visudo -f /etc/sudoers.d/educadata-deploy
```

Contenido:

```
azael.zarate ALL=(root) NOPASSWD: /usr/bin/systemctl restart educadata, /usr/bin/systemctl is-active educadata
```

(Confirmar la ruta con `which systemctl` y que el servicio se llame `educadata.service`;
si tiene otro nombre, cambiar `SERVICE` en el yml y esta línea.)

## 3. Que el repo del servidor pueda hacer pull

En `/tacopy/educadata`, `git pull origin main` debe funcionar sin pedir credenciales
(deploy key SSH de solo lectura, o remoto https con token). Probar a mano una vez:

```bash
cd /tacopy/educadata && git pull --ff-only origin main
```

El pull es `--ff-only`: si alguien editó archivos versionados directamente en el
servidor, el deploy falla en vez de pisar cambios.

## 4. Pasos manuales después de un deploy (cuando apliquen)

```bash
cd /tacopy/educadata
set -a; source .env; set +a
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart educadata
```

## Notas

- `gunicorn` no está en `requirements.txt`; vive solo en el `.venv` del servidor.
  Conviene agregarlo para que un venv nuevo no rompa el servicio.
- Para desplegar otra rama: cambiar `branches:` y `BRANCH` en el yml.
- Deploy manual: pestaña Actions -> Deploy educadata -> Run workflow.
