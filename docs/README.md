<p align="center">
  <a href="https://docs.learnhouse.app">
    <img alt="LearnHouse" src=".github/images/learnhouse-github.png" width="600" />
  </a>
</p>

<p align="center">
  <strong>LearnHouse Documentation</strong>
</p>

<p align="center">
  LearnHouse-derived documentation site retained in the Studytomy fork. Studytomy-specific engineering evidence is under <code>docs/studytomy/</code>.
</p>

<p align="center">
  <a href="https://docs.learnhouse.app">docs.learnhouse.app</a>
</p>

---

## Local Development

This site lives under `docs/` in the Studytomy monorepo. Run commands from that directory. For the LMS runtime rather than this documentation site, follow the [root README](../README.md) and [local-only guidance](../LOCAL-ONLY.md).

**Prerequisites:** [Bun](https://bun.sh) installed.

```bash
# Clone the monorepo and move into the docs app
git clone https://github.com/nirzaf/studytomyportal.git
cd studytomyportal/docs

# Install dependencies
bun install --frozen-lockfile

# Start the dev server
bun dev
```

The site will be available at `http://localhost:3000`.

## Project Structure

```
content/          # MDX documentation pages
  getting-started/
  platform/
  self-hosting/
  developers/
  enterprise/
  guides/
  cli/
app/              # Next.js App Router
components/       # React components
public/           # Static assets
scripts/          # Build scripts
```

## Built With

- [Next.js](https://nextjs.org)
- Fumadocs Core `16.18.1` and MDX `15.5.0` (declared in [package.json](package.json))
- [Tailwind CSS](https://tailwindcss.com)

## License

See the repository [LICENSE](../LICENSE) (AGPL-3.0) and retain applicable third-party notices. This directory has no separate `LICENSE` file; do not infer a separate MIT grant from this README.
