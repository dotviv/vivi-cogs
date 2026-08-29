# vivi-cogs

Cogs for [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot) v3, by **vivirancy**.

## Installation

Replace `[p]` with your bot's prefix.

```
[p]load downloader
[p]repo add vivi-cogs https://github.com/vivirancy/vivi-cogs
[p]cog install vivi-cogs verification
[p]load verification
```

Requires Red 3.5.0 or newer. Dependencies (Pillow) are installed automatically.

## Cogs

| Cog | Description |
| --- | --- |
| `verification` | Gate new members behind an image captcha delivered through a button panel, with configurable roles to add and remove on success. |
| `topics` | Suggest random conversation starters, and let anyone anonymously ask the channel to change the subject. |

## Verification — quick start

The cog ships **disabled**. Configure it first, then turn it on:

```
[p]verifyset channel #verification
[p]verifyset joinrole add @Unverified      # applied when someone joins
[p]verifyset removerole add @Unverified    # stripped once they pass
[p]verifyset addrole add @Member           # granted once they pass
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

Note that Discord's popup form covers the screen, so the captcha is not visible while
someone is typing — they have to memorize it. If six characters proves awkward for
your members, `[p]verifyset length 4` shortens it.

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

## Topics — quick start

The cog works the moment it loads, with 60 built-in conversation starters. Two things
are worth doing straight away:

```
[p]cog install <repo> modlog      # required: requests are recorded as modlog cases
[p]load modlog
[p]topicset settings              # review everything
```

### Enable slash commands

**Do this.** `[p]changetopic` is only truly anonymous as a slash command, and slash
commands have to be turned on by the bot owner:

```
[p]slash enablecog Topics
[p]slash sync
```

With `/changetopic`, nothing the requester does is ever visible — Discord shows the
invocation to no one but them, and the bot's reply is private.

The prefix version is a fallback. It works by **deleting the command message**, which
needs the **Manage Messages** permission and still leaves a brief window where anyone
watching the channel could see who typed it. If the bot cannot delete the message, the
request is not sent at all and the member is told to use the slash command instead —
better a failed request than one with a name attached.

### How a request works

1. Someone runs `/changetopic`, optionally with a note.
2. The channel gets a nameless embed: *someone here would like to move on*, plus the
   note if there was one.
3. A modlog case is opened naming the requester as its target, quoting the note, and
   linking straight to the public notice so a moderator can jump into the
   conversation. It shows up in `[p]cases @them` alongside everything else.
4. The requester gets a private confirmation.

That last confirmation is a DM when the prefix command is used, and `[p]topicset dm`
turns it off for servers that would rather the bot not message members unprompted. It
only applies to the prefix command — `/changetopic` confirms privately inside Discord
and never sends a DM either way.

Turning it off silences the *success* message only. A member is always told when a
request **fails**, because a silent failure would leave someone believing they'd been
heard when they hadn't.

Notes are posted by the bot with mentions disabled, so an anonymous note can never be
used to ping the server from behind the bot.

### Who can see what

This is the part to get right. A request is anonymous **in the channel it was made in,
and nowhere else** — the modlog case names the requester, because spotting someone
leaning on the feature is the whole point of recording it.

Requests are deliberately **never** written to Red's built-in modlog. Core's `[p]case`
and `[p]casesfor` carry no permission check, so any member could look up who filed a
request. The ModLog cog gates `[p]case` and `[p]cases` behind mod permissions, which is
why it is required here rather than optional.

If ModLog is not loaded, requests still appear in the channel anonymously, but nothing
is recorded and no one is told who made them — Topics will not fall back to core rather
than un-anonymise a requester.

### Topics

`[p]topic` draws from the 60 built-in starters plus anything you add with
`[p]topicset add`. Recently-used topics are skipped so the same one doesn't come up
twice in a row. To run on your own list alone, add some topics and then turn the
built-ins off with `[p]topicset defaults`.

## Command reference

### Setup — `[p]verifyset` (requires Manage Server)

| Command | Description |
| --- | --- |
| `channel <channel>` | Set the channel the panel lives in |
| `panel` | Post the Verify panel, or refresh an existing one in place |
| `joinrole add\|remove <role>` | Roles applied the moment someone joins |
| `addrole add\|remove <role>` | Roles granted once verification succeeds |
| `removerole add\|remove <role>` | Roles stripped once verification succeeds |
| `attempts <1-10>` | Tries allowed before lockout |
| `timeout <60-900>` | Seconds a captcha stays valid |
| `length <4-10>` | Characters in the code |
| `onfail <none\|kick>` | What happens when someone uses up every attempt |
| `toggle` | Enable or disable verification |
| `settings` | Show the current configuration |
| `test` | Preview a captcha; changes no roles and records no state |

### Moderation — `[p]verify` (requires Manage Roles)

| Command | Description |
| --- | --- |
| `approve <member>` | Pass a member through without solving the captcha |
| `reject <member>` | Lock a member out and apply the failure action |
| `reset <member>` | Clear a lockout so a member can try again |

### Topics — everyone

| Command | Description |
| --- | --- |
| `topic` | Suggest a random conversation topic |
| `changetopic [message]` | Anonymously ask the channel to change the subject, with an optional note |

Both are available as slash commands once the owner runs `[p]slash enablecog Topics`.

### Topics setup — `[p]topicset` (requires Manage Server)

| Command | Description |
| --- | --- |
| `add <topic>` | Add a custom topic |
| `remove <number>` | Remove a custom topic by its number from `list` |
| `list` | Show the custom topics |
| `clear` | Remove every custom topic |
| `defaults` | Turn the 60 built-in topics on or off |
| `cooldown <0-3600>` | Seconds a member must wait between requests; `0` disables |
| `dm` | Whether to DM a member confirming their request went through (prefix only) |
| `toggle` | Enable or disable `changetopic` |
| `settings` | Show the current configuration |

## Development

Shared code lives in `common/` and is **vendored** into each cog as `_common/`.
Red's Downloader installs one folder per cog and nothing else, so a top-level
shared package would resolve in a checkout and vanish on a real install. Edit
`common/`, never a `_common/` copy, then:

```
python tools/sync_common.py           # write the copies
python tools/sync_common.py --check   # verify they are current (CI)
python tools/check_manifests.py       # validate every info.json
```

Because every cog carries its own copy, classes from `_common` must never be
passed between cogs — two cogs' copies are unrelated types at runtime. Cross-cog
calls go through `bot.get_cog(...)` and primitives; see `common/modlog_proxy.py`.

### Tests

```
pip install -r requirements-dev.txt
python -m unittest discover -s tests -t .
```

Stdlib `unittest` only — no test framework to install. The suite runs against
fakes rather than a live bot, so it needs no Discord connection.

## License

[MIT](LICENSE.md)
