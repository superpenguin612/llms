# contrib

Scheduling `kb watch` on a Linux machine with systemd user units:

```sh
ln -sf "$PWD/kb-watch-all" ~/.local/bin/kb-watch-all
mkdir -p ~/.config/systemd/user && ln -sf "$PWD"/systemd/kb-watch.* ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now kb-watch.timer
loginctl enable-linger "$USER"        # keep user timers running when you're logged out
```

Every hour it runs `kb watch run --due --push` in each vault from `kb registry list` (shared vaults only).
Watches commit only the record files they changed and push. A rejected push is rebased once; on a
conflict the commit stays local and the run fails (see `journalctl --user -u kb-watch`).
Put a read-only `GITHUB_TOKEN=...` in `~/.config/kb/env` for GitHub API checks (releases, new repos).
