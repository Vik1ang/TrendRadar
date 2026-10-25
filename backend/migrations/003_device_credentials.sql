CREATE TABLE device_pairing_codes (
  id uuid PRIMARY KEY,
  code_hash bytea NOT NULL UNIQUE,
  expected_device_name text NOT NULL CHECK (char_length(expected_device_name) BETWEEN 1 AND 80),
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  used_at timestamptz,
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts BETWEEN 1 AND 10),
  CHECK (expires_at > created_at)
);
CREATE INDEX device_pairing_codes_expiry_idx ON device_pairing_codes(expires_at) WHERE used_at IS NULL;

CREATE TABLE device_credentials (
  id uuid PRIMARY KEY,
  device_name text NOT NULL CHECK (char_length(device_name) BETWEEN 1 AND 80),
  token_hash bytea NOT NULL UNIQUE,
  scopes text[] NOT NULL DEFAULT ARRAY['news:read']::text[],
  enabled boolean NOT NULL DEFAULT true,
  issued_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  last_used_at timestamptz,
  CHECK (expires_at > issued_at),
  CHECK (scopes <@ ARRAY['news:read']::text[])
);
CREATE INDEX device_credentials_active_expiry_idx ON device_credentials(expires_at) WHERE enabled AND revoked_at IS NULL;
