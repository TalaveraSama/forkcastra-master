# Cómo subirlo a GitHub (GitHub Desktop)

Esta carpeta contiene **todo lo necesario** para publicar el panel Astra WebUI en tu
repositorio `TalaveraSama/forkcastra-master` y crear la release `@@TAG@@`.

```
release/
  entrega/                                  ← esto es lo que se copia al repositorio
  astra-webui-@@VERSION@@-src.tar.gz            el mismo contenido en un tarball
  astra-webui-panel_@@VERSION@@-1_all.deb       panel + agente (sin núcleo)
  SHA256SUMS                                    sumas de comprobación
  RELEASE-NOTES.md                              texto que se pega en la release
  COMO-SUBIRLO.md                               este documento
```

---

## 1. Clonar tu repositorio con GitHub Desktop

1. Abre **GitHub Desktop** → `File` → `Clone repository…`
2. Pestaña **GitHub.com** → busca `forkcastra-master` → elige la carpeta local, por
   ejemplo `C:\Users\TuUsuario\Documents\GitHub\forkcastra-master` → **Clone**.

> No uses `Add local repository` con la carpeta `entrega`: GitHub Desktop necesita el
> repositorio completo (con tus fuentes en C) para poder subir los cambios. La carpeta
> `entrega` sólo se copia **dentro** del clon.

## 2. Copiar la entrega dentro del clon

1. Abre el clon en el Explorador: en GitHub Desktop, `Repository` → `Show in Explorer`.
2. Abre también `release\entrega` y copia **el contenido** (no la carpeta) dentro del
   clon. Deben quedar, junto a tus `main.c`, `core/`, `lua/`, `modules/`…:

```
agent/            agente Lua (se instala como script principal de astra)
panel/            backend Python (astra_webui) + WebUI estática (panel/web)
packaging/        build-deb.sh, unidades systemd, postinst/prerm/postrm, config, wsl/
tools/            herramientas de prueba y de empaquetado
docs/             INSTALL, INSTALL-WSL, ARCHITECTURE, API, TROUBLESHOOTING, RELEASE…
.github/          workflows de GitHub Actions (CI y publicación de la release)
README-astra-webui.md    el README del panel (tu README.md no se toca)
gitignore-astra-webui.txt  líneas para añadir a tu .gitignore
gitattributes-astra-webui.txt  líneas para añadir a tu .gitattributes (saltos de línea LF)
```

3. Abre `.gitignore` del repositorio y **añade** el contenido de
   `gitignore-astra-webui.txt` (evita subir `var/`, `build/`, `dist/`, `*.deb`,
   `__pycache__/`). Después puedes borrar `gitignore-astra-webui.txt`.
4. Abre `.gitattributes` y **añade** el contenido de `gitattributes-astra-webui.txt`:
   obliga a que los scripts y el código usen saltos de línea LF aunque trabajes desde
   Windows (si no, un `core.autocrlf=true` puede dejar los `.sh` con CRLF y romperlos en
   Linux con `bad interpreter: /bin/sh^M`). Después puedes borrar el fichero de texto.

> Nada de lo que se copia **sobrescribe** tus ficheros: el README va con otro nombre y el
> `.gitignore`/`.gitattributes` se entregan aparte a propósito.

## 3. Commit y push

1. En GitHub Desktop verás la lista de ficheros nuevos. Comprueba que **no** aparecen
   `var/`, `build/`, `dist/` ni `__pycache__/` (si aparecen, es que falta el paso 2.3).
2. Resumen del commit, por ejemplo:

   ```
   Añadir panel administrativo web (agente Lua + panel Python + .deb + CI)
   ```

3. **Commit to main** → **Push origin**.

A partir de ese push, la pestaña **Actions** de tu repositorio ejecutará las pruebas
automáticamente (agente Lua, panel, empaquetado y navegador real).

## 4. Crear la release con el `.deb` (recomendado)

> **Estado actual:** la release `@@TAG@@` **ya está publicada** (con el paquete del panel,
> el código fuente y las sumas). Para añadir el **paquete completo con el núcleo**, sube
> antes el código (paso 3) y lanza el workflow a mano con esa misma etiqueta: `Actions` →
> `release` → **Run workflow** → escribe `@@TAG@@`. El workflow detecta que la release ya
> existe y **reemplaza/añade** los ficheros, además de actualizar el texto.
> No hace falta empujar la etiqueta con `git tag` (ya existe y no volvería a lanzar nada).

El `.deb` **completo** (con el núcleo compilado) tiene que compilarse en Linux; para eso
está el workflow incluido. GitHub lo hace por ti:

1. En el repositorio, pestaña **Actions** → workflow **release** → **Run workflow**.
2. En el campo de la etiqueta escribe `@@TAG@@` y pulsa **Run workflow**.
3. En 2–4 minutos tendrás en la release `@@TAG@@`:
   * `astra-webui_@@CORE@@-1_amd64.deb` (núcleo + agente + panel + WebUI)
   * `astra-webui-panel_@@VERSION@@-1_all.deb`
   * `astra-webui-@@VERSION@@-src.tar.gz`
   * `SHA256SUMS`
4. El texto de la release se toma de `docs/RELEASE-NOTES.md` (ya está redactado).

> Si GitHub te pide permisos: `Settings` → `Actions` → `General` →
> *Workflow permissions* → **Read and write permissions** (el workflow lo pide por sí
> mismo, pero algunos repositorios lo tienen bloqueado).

### Alternativa: subirlo a mano

Si prefieres no usar Actions:

1. Pestaña **Releases** → **Draft a new release** → *Choose a tag* → escribe `@@TAG@@` →
   *Create new tag*.
2. Título: `Astra WebUI @@TAG@@`.
3. Descripción: pega el contenido de `RELEASE-NOTES.md`.
4. Adjunta los ficheros de esta carpeta: `astra-webui-panel_@@VERSION@@-1_all.deb`,
   `astra-webui-@@VERSION@@-src.tar.gz` y `SHA256SUMS`.
   El `.deb` **completo** con el núcleo sólo puedes generarlo en un Linux:
   ```sh
   ./packaging/build-deb.sh --source . --out dist
   ```
5. **Publish release**.

GitHub Desktop no crea etiquetas ni releases: para eso usa la web (pasos de arriba) o el
workflow.

## 5. Comprobar la instalación en un Ubuntu 22.04+

```sh
sudo apt install ./astra-webui_@@CORE@@-1_amd64.deb
sudo cat /etc/astra-webui/initial-admin-password
systemctl status astra astra-webui
```

Y abre `http://IP:8000` con el usuario `admin`.

---

## Qué NO hay que subir

| Carpeta | Por qué |
|---|---|
| `repo/` | copia local del fork para el desarrollo: en tu repositorio el código ya está en la raíz |
| `build/`, `dist/`, `release/` | artefactos generados (están en `.gitignore`) |
| `var/` | datos de las pruebas en modo `--dev` |
| `__pycache__/`, `*.pyc` | cachés de Python |

## Problemas frecuentes

| Síntoma | Solución |
|---|---|
| GitHub Desktop dice que la carpeta no es un repositorio | usa **Clone repository** (paso 1), no `Add local repository` sobre `entrega/` |
| El push se rechaza (*rejected / fetch first*) | pulsa **Pull origin** y vuelve a hacer push |
| Aparecen `var/` o `__pycache__/` en los cambios | añade las líneas de `gitignore-astra-webui.txt` al `.gitignore` |
| El workflow `release` falla con "Resource not accessible" | activa *Read and write permissions* en `Settings` → `Actions` → `General` |
| Quiero volver a lanzar la release | `Actions` → `release` → `Run workflow` con la misma etiqueta: los ficheros se reemplazan |
