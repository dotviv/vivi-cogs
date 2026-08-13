# vivi-cogs

Cogs for [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot) v3.

## Installation

Replace `[p]` with your bot's prefix.

```
[p]load downloader
[p]repo add vivi-cogs <repo-url>
[p]cog install vivi-cogs verification
[p]load verification
```

## Cogs

| Cog | Description |
| --- | --- |
| `verification` | Gate new members behind an image captcha in a dedicated channel, with configurable roles to add and remove on success. |

## Verification — quick start

The cog ships **disabled**. Configure it first, then turn it on:

```
[p]verifyset channel #verification
[p]verifyset joinrole add @Unverified     # applied when someone joins
[p]verifyset removerole add @Unverified    # stripped once they pass
[p]verifyset addrole add @Member           # granted once they pass
[p]verifyset settings                      # review everything
[p]verifyset toggle                        # enable
```

Use `[p]verifyset test` to send yourself a captcha without touching any roles.

### Permissions

The bot needs **Manage Roles**, and its highest role must sit *above* every role it
manages. The cog checks this when you configure a role and refuses roles it cannot
assign — silent hierarchy failures are the most common way this kind of cog breaks.

`[p]verifyset onfail kick` additionally requires **Kick Members**. The default
failure action is `none`: a member who fails simply stays unverified and can retry.

## License

[MIT](LICENSE.md)
