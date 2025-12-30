# chain-asset

Community-driven token information repository for [Subscan](https://www.subscan.io). Submit token information via issues, reviewed and merged by the Subscan team.

## How to Submit Token Information

### Step 1: Create an Issue

Click [here](../../issues/new) to create a new issue with the following required information:

#### Required Fields

- **Subscan Link**: Direct link to your token on Subscan (e.g., `https://polkadot.subscan.io/assets/<token_id>`)
- **Icon/Logo**:
  - Format: PNG or SVG (lowercase extension only)
  - Size: Less than 30KB
  - Background: Preferably transparent
  - Provide either:
    - Direct link to the logo file
    - Attach the logo file to the issue
- **CoinGecko API ID (Optional)**: Your token's API ID on CoinGecko (if available, otherwise leave empty)
- **Project Information (Optional)**:
  - **Description (English)**: Brief description of your token/project
  - **Description (Chinese)**: 简体中文描述 (optional)
  - **Website Link**: Official website URL
  - **Social Links** (optional but recommended):
    - Twitter/X Link
    - Telegram Link
    - Discord Link
    - Medium Link
    - GitHub Link
  - **Tag**: Usage or feature indicators (e.g., `DeFi`, `NFT`, `Gaming`)
  - **Risk Level**: One of `normal`, `unsafe`, `scam` (default: `normal`)

### Step 2: Issue Template Example

```markdown
**Subscan Link**: https://polkadot.subscan.io/assets/1234

**Icon**: [Attach file or provide link]

**CoinGecko API ID**: abc-token

**Project Information**:
- Description (EN): ABC is a governance token for...
- Description (ZH): ABC 是一个治理代币...
- Website: https://abc-token.io
- Twitter: https://twitter.com/abc_token
- Telegram: https://t.me/abc_token
- Discord: https://discord.gg/abc
- GitHub: https://github.com/abc-token
- Tag: DeFi, Governance
- Risk: normal
```

### Step 3: Review

- Subscan team will review your submission
- You may be asked to provide additional information

### Step 4: Updates Reflected on Subscan

After approval, token information will be updated on Subscan within 2-3 business days.

## Categories

## Logo Naming Convention

If providing logo files directly:
- Format: `<network>_<category>_<symbol>.<png/svg>`
- Example: `polkadot_asset_ABC.png`
- Size: < 30KB
- Background: Transparent preferred

## FAQ

**Q: How long does the review process take?**
A: Typically 3-5 business days, depending on submission info and complexity.

**Q: Can I update existing token information?**
A: Yes, create a new issue with "Update" in the title and reference the existing token.

**Q: What if I don't have a CoinGecko listing?**
A: Leave the CoinGecko API ID field empty.

**Q: My network is not in the supported list**
A: Contact the Subscan team via [our support channels](https://support.subscan.io) to request network addition.

## Contact & Support

- Issues: [GitHub Issues](../../issues)
- Documentation: [Subscan Docs](https://docs.subscan.io)
- Support: [support.subscan.io](https://support.subscan.io)

---

**Note**: This repository uses an issue-based submission workflow. Direct pull requests to modify data files will not be accepted. All submissions must go through the issue review process.
