# The Bridge Site Notes

This is the internal web download/update site.

## Production TAE App

- App name: `thebridge`
- Domain: `https://thebridge.tae.vera-mesh.com`
- Current long-running image:
  `registry.pixcakeai.com/tae/the-bridge-site:data-static-202606111538`
- Port: `80`
- Storage: `10Gi`, mounted at `/data`

## Static Resource Layout

Large resources must live in TAE `/data`, not inside the image:

```text
/data/thebridge/
  resources/
    The-Bridge.dmg
  updates/
    manifest.json
    code-x.y.z.zip
```

Nginx maps:

```text
/resources/* -> /data/thebridge/resources/*
/updates/*   -> /data/thebridge/updates/*
```

Public URLs:

```text
https://thebridge.tae.vera-mesh.com/resources/The-Bridge.dmg
https://thebridge.tae.vera-mesh.com/updates/manifest.json
```

Replacing a DMG, update zip, or manifest should not require rebuilding the web
image. Rebuild/deploy the image only when `index.html`, `styles.css`,
`script.js`, `nginx/default.conf`, or related routing changes.

## Image Deploys

Use the vendored TAE skills:

```bash
ts-skill use tae-image-release --no-install
ts-skill use tae-app-manager
```

If the `ts-skill` shim is absent, use:

```bash
~/.codex/skills/ts-skill-platform/scripts/ts-skill use tae-image-release --no-install
```

The final web image should be light and should not COPY `resources/`.
Temporary seed images are acceptable only to copy files into `/data`, then the
app must be switched back to the long-running light image.
