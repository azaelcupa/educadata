# CI/CD de educadata (GitHub Actions, runner self-hosted)

Flujo: push a `main` -> el runner en el servidor hace `git pull`, instala
`requirements.txt` en `/tacopy/educadata/.venv` y reinicia el servicio `gunicorn-educadata`
(gunicorn en 10.3.29.160:8700). `migrate` se corre en local antes del push; los estáticos se manejan aparte (ver sección 4).

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
azael.zarate ALL=(root) NOPASSWD: /usr/bin/systemctl restart gunicorn-educadata, /usr/bin/systemctl is-active gunicorn-educadata
```

(Confirmar la ruta con `which systemctl` y que el servicio se llame `gunicorn-educadata.service`;
si tiene otro nombre, cambiar `SERVICE` en el yml y esta línea.)

## 3. Que el repo del servidor pueda hacer pull

En `/tacopy/educadata`, `git pull origin main` debe funcionar sin pedir credenciales
(deploy key SSH de solo lectura, o remoto https con token). Probar a mano una vez:

```bash
cd /tacopy/educadata && git pull --ff-only origin main
```

El pull es `--ff-only`: si alguien editó archivos versionados directamente en el
servidor, el deploy falla en vez de pisar cambios.

## 4. Migraciones y estáticos

El pipeline no corre `migrate` ni `collectstatic`:

- `migrate` se corre en local antes de hacer push, para revisar errores ahí.
  Los archivos de migración generados viajan en el commit; en el servidor solo se
  aplican cuando haga falta, a mano:
  `cd /tacopy/educadata && set -a; source .env; set +a; .venv/bin/python manage.py migrate`
- Los estáticos se comparten entre varios proyectos del servidor y se gestionan
  aparte; no se tocan en el deploy.

## Notas

- `gunicorn` no está en `requirements.txt`; vive solo en el `.venv` del servidor.
  Conviene agregarlo para que un venv nuevo no rompa el servicio.
- Para desplegar otra rama: cambiar `branches:` y `BRANCH` en el yml.
- Deploy manual: pestaña Actions -> Deploy educadata -> Run workflow.
