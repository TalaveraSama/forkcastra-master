# Publicar una release

Resumen del proceso, de dónde sale cada fichero y cómo comprobarlo.

## Versiones y etiquetas

| Cosa | Valor |
|---|---|
| Versión del núcleo | la del fork: `4.0.282` (`version.h`, campo `ASTRA_VERSION_DEV`) |
| Versión del panel | la de `tools/deb_stage.py` (`PANEL_VERSION`, hoy `1.0.0`) |
| Nombres de los paquetes | `astra-webui_<núcleo>-1_amd64.deb` (completo) y `astra-webui-panel_<panel>-1_all.deb` |
| Etiqueta de la release | `v<núcleo>-<n>`, continuando tu serie: la siguiente es `v4.0.282-10` |

## Ficheros que lleva una release

| Fichero | Quién lo genera | Para qué |
|---|---|---|
| `astra-webui_<núcleo>-1_amd64.deb` | `packaging/build-deb.sh` (Linux con gcc) | instalación completa: núcleo + agente + panel + WebUI |
| `astra-webui-panel_<panel>-1_all.deb` | `tools/deb_stage.py --no-core` | sólo agente + panel + WebUI (para quien ya tiene el núcleo) |
| `astra-webui-<panel>-src.tar.gz` | `tools/make_release.py` o el workflow | código del panel y del agente, para revisar o compilar a mano |
| `SHA256SUMS` | `tools/make_release.py` o el workflow | sumas SHA-256 (`sha256sum -c SHA256SUMS`) |
| `RELEASE-NOTES.md` | `docs/RELEASE-NOTES.md` con las variables sustituidas | texto de la release |

## Camino recomendado: GitHub Actions

> **Estado actual:** la etiqueta `v4.0.282-10` y su release **ya existen** (publicadas con
> el paquete del panel, el código fuente y `SHA256SUMS`). Como la etiqueta ya está creada,
> empujarla otra vez no lanza nada: hay que lanzar el workflow **a mano** con esa etiqueta
> (`Actions` → `release` → *Run workflow*). El workflow detecta la release existente y le
> añade el `.deb` completo con el núcleo.

El workflow `.github/workflows/release.yml` compila los `.deb` en Ubuntu y publica la
release con los ficheros adjuntos. Se lanza de dos formas:

```sh
# con git (sólo para etiquetas NUEVAS)
git tag v4.0.282-11
git push origin v4.0.282-11
```

o desde la web (**la que toca ahora**): **Actions → release → Run workflow** y escribir la
etiqueta (`v4.0.282-10`). La segunda forma no depende de `git` ni de etiquetas nuevas.

Qué hace el workflow:

1. Tres compilaciones en paralelo: **Ubuntu 20.04** (amd64, el binario vale para 20.04,
   22.04 y 24.04), **Ubuntu 22.04** nativo y **ARM64** (opcional: si los runners `arm` no
   están disponibles, el resto continúa).
2. Localiza el código del fork: la raíz del repositorio si el repositorio **es** el fork,
   `repo/tree` si es el árbol de desarrollo, o clona el fork si sólo hay panel.
3. Compila, monta los paquetes con `tools/deb_stage.py`, los verifica con
   `tools/build_deb.py --verify` y calcula `SHA256SUMS`.
4. Crea la release (o actualiza sus ficheros si ya existía) con las notas de
   `docs/RELEASE-NOTES.md`, sustituyendo `@@TAG@@`, `@@CORE@@` y `@@VERSION@@`.

Permisos: el workflow declara `permissions: contents: write`. Si el repositorio tiene
*Settings → Actions → General → Workflow permissions* en **read-only**, cámbialo a
**Read and write** o el paso final fallará con *Resource not accessible by integration*.

## Camino manual (sin Actions)

En una máquina Ubuntu 22.04+:

```sh
sudo apt install -y build-essential python3 git
git clone https://github.com/TalaveraSama/forkcastra-master.git
cd forkcastra-master
./packaging/build-deb.sh --source . --out dist      # .deb completo
python3 tools/make_release.py --tag v4.0.282-10     # entrega + panel .deb + sumas + notas
```

Después, en GitHub: **Releases → Draft a new release**, crear la etiqueta y adjuntar los
ficheros de `release/`. Desde Windows no hace falta nada de esto: el panel se prepara con
`python3 tools/make_release.py` y el `.deb` completo lo genera el workflow (o tu servidor).

## Comprobar una release antes de instalarla

```sh
sha256sum -c SHA256SUMS
dpkg-deb -I astra-webui_4.0.282-1_amd64.deb        # control: versión, dependencias
dpkg-deb -c astra-webui_4.0.282-1_amd64.deb | head # contenido
```

## Instalación en Ubuntu 22.04 o superior

```sh
sudo apt install ./astra-webui_4.0.282-1_amd64.deb
sudo cat /etc/astra-webui/initial-admin-password
systemctl status astra astra-webui
```

Si el servidor ya tiene Astra 5.x u otra instalación que use `/usr/bin/astra` o
`astra.service`, revisa antes el conflicto: este paquete instala el binario como
`/usr/bin/astra-4.0` con enlace `/usr/bin/astra` y los servicios `astra.service` y
`astra-webui.service`. Para una instalación en paralelo sin tocar nada, avísame y preparo
la variante con prefijo propio (`forkcastra`), que es lo que describían tus releases
anteriores.

## Pruebas automáticas

`.github/workflows/ci.yml` ejecuta en cada push: comprobaciones estáticas de la WebUI,
agente Lua (117), panel (147), empaquetado (157) y las 44 comprobaciones en un Chrome real
sin interfaz. En local, lo mismo con:

```sh
python3 tools/run_tests.py            # todas las suites y resumen
python3 tools/run_tests.py --quick    # sin navegador
```

## Historial

| Etiqueta | Contenido |
|---|---|
| `v4.0.282-1` … `v4.0.282-9` | releases anteriores **sin ficheros adjuntos** (sólo notas): no se pueden instalar tal cual |
| `v4.0.282-10` | primera con ficheros adjuntos: panel (`.deb`), código fuente y sumas; el `.deb` completo lo añade el workflow |
