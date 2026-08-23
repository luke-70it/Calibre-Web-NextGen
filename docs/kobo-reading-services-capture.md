# Kobo Reading Services private exchange capture

This diagnostic is for short, operator-controlled hardware experiments. It is
off by default and cannot be enabled with an ordinary boolean value.

Enable it by setting this exact environment value and restarting CWNG:

```text
CWNG_KOBO_READING_SERVICES_CAPTURE=I_UNDERSTAND_THIS_CAPTURES_PRIVATE_READING_DATA
```

Unset the variable and restart immediately after the experiment. Values such as
`1`, `true`, different case, or values with surrounding whitespace do not
enable capture.

Records are written to:

```text
<config>/.cwng-private-observability/kobo-reading-services/
```

In the standard container `<config>` is `/config`. The directory is mode 0700
and each `exchange-*.json.gz` record is mode 0600. Records are not copied into
the annotation backup format or the support debug ZIP, and no record belongs in
a repository, issue attachment, or other shared artifact. External backup jobs
that archive all of `/config` should explicitly exclude
`.cwng-private-observability/`.

Each schema-version-1 record contains:

- the device request body and redacted headers;
- `checkforchanges` decisions in original array order, including ownership,
  observed authority state, and whether each ID was suppressed or proxied;
- the exact request body actually sent to Kobo after filtering;
- Kobo's raw response body and redacted headers; and
- the final status, redacted headers, and exact body returned to the device.

Bodies carry byte length and SHA-256 metadata. UTF-8 bodies are stored directly;
any non-UTF-8 body is base64 encoded. Credential-like headers—including
Authorization, cookies, Kobo user keys, API keys, secrets, and tokens—are
replaced with `***REDACTED***` in every leg.

Retention is automatic and cross-process locked: at most 256 records, 64 MiB
compressed total, seven days, and 16 MiB for any individual body. An exchange
above the body limit is skipped whole rather than saved partially. Any observer
or storage failure is logged only with structural metadata and cannot replace,
delay with retries, or change the response being observed.
