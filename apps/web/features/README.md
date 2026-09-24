# Features

One folder per business feature, mirroring a domain in
`apps/api/app/domains/<name>/`. A feature folder owns everything the feature
needs beyond routing:

```
features/<name>/
├── components/   feature-specific components
├── hooks/        feature-specific hooks
├── api.ts        calls into lib/api for this feature's endpoints
├── types.ts      shared types for this feature
└── utils.ts      pure helpers
```

Rules:

- Routes in `app/` stay thin — pages compose features, logic lives here.
- New feature = spec in `.ai/features/<name>.md` first.
- Feature folders do not import each other's internals; shared pieces move up
  to `components/ui` or `lib`.
