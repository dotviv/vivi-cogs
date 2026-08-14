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
| `verification` | Gate new members behind an image captcha delivered through a button panel, with configurable roles to add and remove on success. |

## Verification — quick start

The cog ships **disabled**. Configure it first, then turn it on:

```
[p]verifyset channel #verification
[p]verifyset joinrole add @Unverified      # applied when someone joins
[p]verifyset removerole add @Unverified    # stripped once they pass
[p]verifyset addrole add @Member           # granted once they pass
[p]verifyset modlog #staff-log             # optional: log outcomes
[p]verifyset panel                         # post the Verify button
[p]verifyset settings                      # review everything
[p]verifyset toggle                        # enable
```

Use `[p]verifyset test` to send yourself a captcha without touching any roles.

### How a member verifies

1. They press **Verify** on the panel.
2. A captcha appears that **only they can see**.
3. They press **Enter Code** and type it into a popup form.
4. Their roles are updated.

Nothing is ever posted publicly, so no one can read anyone else's code.

### Channel permissions

Because the whole exchange is private, the verification channel needs no public
writing at all. For unverified members, allow **View Channel** and **Read Message
History**, and **deny Send Messages**. `[p]verifyset panel` warns you if `@everyone`
can still post there.

The bot itself needs **Send Messages**, **Embed Links**, and **Attach Files** in that
channel.

### Roles

The bot needs **Manage Roles**, and its highest role must sit *above* every role it
manages. The cog checks this when you configure a role and refuses roles it cannot
assign — silent hierarchy failures are the most common way this kind of cog breaks.

### Failure handling

Attempts persist across button presses, so a member cannot reset their allowance by
clicking Verify again. At zero attempts they are locked out until a moderator runs
`[p]verify reset @member`.

`[p]verifyset onfail kick` additionally requires **Kick Members**. The default is
`none`: a member who runs out of attempts stays unverified and waits for a reset.

**Running out of time never kicks and never costs an attempt.** If someone presses
Verify and then walks away, the captcha is simply discarded and they can start over.
Only submitting wrong codes until no attempts remain triggers `onfail`.

That leaves one deliberate gap: a member who joins and never presses Verify is never
actioned at all. They keep the join role and no access until you remove them yourself.

## License

[MIT](LICENSE.md)
