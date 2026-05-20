# Development — testing in-progress iblai apps against a local stack

This guide documents how to develop and test an iblai app (e.g. `ibl-edx-magic-link-login-app`) against a locally running `ibl edx` (Tutor) deployment by **bind-mounting your working tree into the LMS/CMS containers**. Code changes on the host are reflected inside the running containers without rebuilding the image.

## 1. Layout the host directory

Keep all in-development app sources in a single, predictable directory on the host so override paths are stable across machines:

```bash
mkdir -p ~/github
cd ~/github
git clone -b <feature-branch> git@github.com:iblai/<app-name>.git
```

Example:

```bash
git clone -b feature/login-flow git@github.com:iblai/ibl-edx-magic-link-login-app.git
```

After cloning, the package source lives at:

```
/home/ubuntu/github/<app-name>/src/<python_package>
```

For the `ibl-edx-magic-link-login-app` example, that is:

```
/home/ubuntu/github/ibl-edx-magic-link-login-app/src/ibl_edx_magic_link_login
```

This **host path** is the left-hand side of every bind mount below.

## 2. Override file location

Tutor reads a per-environment `docker-compose.override.yml` from:

```
/ibl/app/ibl-edx/ibl-edx-pro/env/local/docker-compose.override.yml
```

Create the file there (or edit it if it already exists). The override is merged on top of Tutor's generated `docker-compose.yml`, so you only need to declare the services and volumes you want to add.

The **right-hand side** of each volume (the in-container path) depends on the Tutor release. See §3 and §4.

### Which release am I on?

Before picking between the Sumac (§3) and Olive (§4) override, confirm the release running on the host:

```bash
ibl config printvalue IBL_EDX.VERSION
```

Prints either `sumac` or `olive` — that decides which section applies. Use the Sumac block for `sumac`, the Olive block for `olive`. The mount paths are not interchangeable.

## 3. Sumac override

On Sumac, app sources live under the `edx-platform/requirements/` tree and the path is **not** version-pinned. The mount target is simply:

```
/openedx/edx-platform/requirements/<app-name>/src/<python_package>
```

Full override for `ibl-edx-magic-link-login-app` on Sumac:

```yaml
services:
  ############# LMS and CMS workers
  lms:
    volumes:
      - /home/ubuntu/github/ibl-edx-magic-link-login-app/src/ibl_edx_magic_link_login:/openedx/edx-platform/requirements/ibl-edx-magic-link-login-app/src/ibl_edx_magic_link_login
  lms-worker:
    volumes:
      - /home/ubuntu/github/ibl-edx-magic-link-login-app/src/ibl_edx_magic_link_login:/openedx/edx-platform/requirements/ibl-edx-magic-link-login-app/src/ibl_edx_magic_link_login
  cms:
    volumes:
      - /home/ubuntu/github/ibl-edx-magic-link-login-app/src/ibl_edx_magic_link_login:/openedx/edx-platform/requirements/ibl-edx-magic-link-login-app/src/ibl_edx_magic_link_login
  cms-worker:
    volumes:
      - /home/ubuntu/github/ibl-edx-magic-link-login-app/src/ibl_edx_magic_link_login:/openedx/edx-platform/requirements/ibl-edx-magic-link-login-app/src/ibl_edx_magic_link_login
```

## 4. Olive override

On Olive, app sources live under `/openedx/requirements/` **with the version baked into the directory name** — e.g. `ibl-edx-magic-link-login-app-1.2.1`. You cannot guess this from the host; you must read it from the running container.

### 4a. Discover the in-container path

```bash
ibl tutor local run lms bash
# inside the container:
pip list | grep ibl
# or, scoped to a single package:
pip list | grep <app-name>
```

You will see something like:

```
ibl-edx-magic-link-login-app  1.2.1
```

The corresponding source path inside the container is:

```
/openedx/requirements/ibl-edx-magic-link-login-app-1.2.1/src/ibl_edx_magic_link_login
```

### 4b. Override

```yaml
services:
  lms:
    volumes:
      - /home/ubuntu/github/ibl-edx-magic-link-login-app/src/ibl_edx_magic_link_login:/openedx/requirements/ibl-edx-magic-link-login-app-1.2.1/src/ibl_edx_magic_link_login
  lms-worker:
    volumes:
      - /home/ubuntu/github/ibl-edx-magic-link-login-app/src/ibl_edx_magic_link_login:/openedx/requirements/ibl-edx-magic-link-login-app-1.2.1/src/ibl_edx_magic_link_login
  cms:
    volumes:
      - /home/ubuntu/github/ibl-edx-magic-link-login-app/src/ibl_edx_magic_link_login:/openedx/requirements/ibl-edx-magic-link-login-app-1.2.1/src/ibl_edx_magic_link_login
  cms-worker:
    volumes:
      - /home/ubuntu/github/ibl-edx-magic-link-login-app/src/ibl_edx_magic_link_login:/openedx/requirements/ibl-edx-magic-link-login-app-1.2.1/src/ibl_edx_magic_link_login
```

When the app version bumps, **the right-hand path changes**. Re-run the discovery step in §4a and update the override accordingly.

## 5. Apply the override and iterate

First time after creating or changing the override file, fully recycle edX so Compose re-reads it:

```bash
ibl edx stop && ibl edx start -d
```

For subsequent **code changes** in the bind-mounted directory, you only need to restart the affected services so Python re-imports them:

```bash
ibl tutor local restart lms cms
# or just one:
ibl tutor local restart lms
# include workers if your code runs in Celery tasks:
ibl tutor local restart lms lms-worker cms cms-worker
```

## 6. Mount selectively

The four services in the override are independent — mount only what your app actually touches:

| Code runs in… | Mount into |
|---|---|
| LMS request/response cycle | `lms` |
| CMS (Studio) request/response cycle | `cms` |
| LMS Celery tasks | `lms-worker` |
| CMS Celery tasks | `cms-worker` |

A login flow, for example, typically only needs `lms` (and possibly `lms-worker` for any async post-login work). Adding the others is harmless but slows down restarts.

## 7. Troubleshooting

- **Changes don't show up:** confirm the mount with `ibl tutor local run lms bash` then `ls -la <in-container-path>` — you should see the host owner/timestamps, not the image's.
- **Olive path mismatch:** the version in §4a must match the directory name on disk **exactly**, including the `-X.Y.Z` suffix. A stale version (e.g. an image bump) silently shadows your mount with the packaged source.
- **Override ignored:** verify the file is at `/ibl/app/ibl-edx/ibl-edx-pro/env/local/docker-compose.override.yml` (not `/ibl/...` or a Tutor plugin path) and that `ibl edx stop && ibl edx start -d` was run after the last edit to the override itself (restart alone is not enough when the override file changes).
