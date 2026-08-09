import { useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { KeyRound, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { getWebAuthnCredential } from '@/lib/auth/webauthn-browser';
import {
  AuthApiError,
  type LoginMfaRequired,
  useAuthStore,
} from '@/stores/auth';

interface LoginMfaFormProps {
  pending: LoginMfaRequired;
  onAuthenticated: () => void;
  onBack: () => void;
}

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof AuthApiError) return error.detail;
  if (error instanceof DOMException && error.name === 'NotAllowedError') {
    return 'Passkey verification was cancelled or timed out.';
  }
  return error instanceof Error ? error.message : fallback;
}

export function LoginMfaForm({
  pending,
  onAuthenticated,
  onBack,
}: LoginMfaFormProps) {
  const { t } = useTranslation();
  const completeCode = useAuthStore((state) => state.completeLoginMfaCode);
  const completeWebAuthn = useAuthStore(
    (state) => state.completeLoginMfaWebAuthn,
  );
  const refreshOptions = useAuthStore(
    (state) => state.refreshLoginWebAuthnOptions,
  );
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState<'webauthn' | 'code' | null>(null);
  const [error, setError] = useState('');
  const supportsPasskey = pending.methods.includes('webauthn');
  const supportsCode = pending.methods.includes('totp');
  const supportsRecovery = pending.methods.includes('recovery');

  const verifyPasskey = async () => {
    setBusy('webauthn');
    setError('');
    try {
      const options = pending.webauthnOptions
        ?? await refreshOptions(pending.loginChallenge);
      const credential = await getWebAuthnCredential(options);
      await completeWebAuthn(pending.loginChallenge, credential);
      onAuthenticated();
    } catch (reason) {
      setError(errorMessage(reason, t('auth_error_network', 'Network error. Please retry.')));
      setBusy(null);
    }
  };

  const verifyCode = async (event: FormEvent) => {
    event.preventDefault();
    if (!code.trim()) return;
    setBusy('code');
    setError('');
    try {
      await completeCode(pending.loginChallenge, code.trim());
      onAuthenticated();
    } catch (reason) {
      setError(errorMessage(reason, t('auth_error_network', 'Network error. Please retry.')));
      setBusy(null);
    }
  };

  return (
    <div className="space-y-5" data-testid="login-mfa-form">
      <div className="flex items-start gap-3 rounded-md border border-edge-subtle bg-muted/30 p-3">
        <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
        <div className="space-y-1">
          <p className="text-sm font-medium">
            {t('auth_mfa_title', 'Complete two-step verification')}
          </p>
          <p className="text-xs text-muted-foreground">
            {t(
              'auth_mfa_description',
              'Your password was accepted. Use an enrolled factor to finish signing in.',
            )}
          </p>
        </div>
      </div>

      {supportsPasskey ? (
        <Button
          type="button"
          className="w-full"
          disabled={busy !== null}
          onClick={() => void verifyPasskey()}
        >
          <KeyRound className="mr-2 h-4 w-4" aria-hidden="true" />
          {busy === 'webauthn'
            ? t('security.verifying', 'Verifying…')
            : t('security.usePasskey', 'Use passkey')}
        </Button>
      ) : null}

      {supportsPasskey && supportsCode ? (
        <div className="flex items-center gap-3" aria-hidden="true">
          <span className="h-px flex-1 bg-border" />
          <span className="text-xs text-muted-foreground">
            {t('auth_mfa_or', 'or')}
          </span>
          <span className="h-px flex-1 bg-border" />
        </div>
      ) : null}

      {supportsCode ? (
        <form className="space-y-3" onSubmit={(event) => void verifyCode(event)}>
          <div className="grid gap-2">
            <Label htmlFor="login-mfa-code">
              {supportsRecovery
                ? t('auth_mfa_code_or_recovery', 'Authenticator or recovery code')
                : t('auth_mfa_code', 'Authenticator code')}
            </Label>
            <Input
              id="login-mfa-code"
              value={code}
              autoComplete="one-time-code"
              inputMode="text"
              maxLength={64}
              disabled={busy !== null}
              onChange={(event) => setCode(event.target.value)}
            />
          </div>
          <Button
            type="submit"
            variant={supportsPasskey ? 'outline' : 'default'}
            className="w-full"
            disabled={busy !== null || !code.trim()}
          >
            {busy === 'code'
              ? t('security.verifying', 'Verifying…')
              : t('auth_mfa_verify', 'Verify and sign in')}
          </Button>
        </form>
      ) : null}

      {error ? (
        <p
          role="alert"
          className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
        >
          {error}
        </p>
      ) : null}

      <Button
        type="button"
        variant="ghost"
        className="w-full"
        disabled={busy !== null}
        onClick={onBack}
      >
        {t('auth_mfa_back', 'Back to password')}
      </Button>
    </div>
  );
}
