import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { KeyRound, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  STEP_UP_REQUEST_EVENT,
  type StepUpRequestDetail,
} from '@/lib/auth/step-up-broker';
import {
  createWebAuthnCredential,
  getWebAuthnCredential,
} from '@/lib/auth/webauthn-browser';
import {
  beginWebAuthnAuthentication,
  beginWebAuthnRegistration,
  finishWebAuthnAuthentication,
  finishWebAuthnRegistration,
  getWebAuthnStatus,
} from '@/lib/api/mfa';

type Mode = 'loading' | 'authenticate' | 'enroll';

function messageFor(error: unknown): string {
  if (error instanceof DOMException && error.name === 'NotAllowedError') {
    return 'Passkey verification was cancelled or timed out.';
  }
  return error instanceof Error ? error.message : String(error);
}

export function StepUpDialog() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<Mode>('loading');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [password, setPassword] = useState('');
  const [credentialName, setCredentialName] = useState('Passkey');
  const requestRef = useRef<StepUpRequestDetail | null>(null);

  const finish = (succeeded: boolean) => {
    requestRef.current?.complete(succeeded);
    requestRef.current = null;
    setOpen(false);
    setBusy(false);
    setError('');
    setPassword('');
  };

  useEffect(() => {
    const onRequest = (rawEvent: Event) => {
      const event = rawEvent as CustomEvent<StepUpRequestDetail>;
      event.preventDefault();
      requestRef.current = event.detail;
      setOpen(true);
      setMode('loading');
      setError('');
      void getWebAuthnStatus()
        .then((status) => setMode(status.enabled ? 'authenticate' : 'enroll'))
        .catch((reason) => {
          setError(messageFor(reason));
          setMode('authenticate');
        });
    };
    window.addEventListener(STEP_UP_REQUEST_EVENT, onRequest);
    return () => {
      window.removeEventListener(STEP_UP_REQUEST_EVENT, onRequest);
      requestRef.current?.complete(false);
      requestRef.current = null;
    };
  }, []);

  const authenticate = async () => {
    setBusy(true);
    setError('');
    try {
      const options = await beginWebAuthnAuthentication();
      const credential = await getWebAuthnCredential(options);
      await finishWebAuthnAuthentication(credential);
      finish(true);
    } catch (reason) {
      setError(messageFor(reason));
      setBusy(false);
    }
  };

  const enroll = async () => {
    if (!password) return;
    setBusy(true);
    setError('');
    try {
      const options = await beginWebAuthnRegistration(password);
      const credential = await createWebAuthnCredential(options);
      await finishWebAuthnRegistration(
        credential,
        credentialName.trim() || 'Passkey',
      );
      finish(true);
    } catch (reason) {
      setError(messageFor(reason));
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !busy) finish(false);
      }}
    >
      <DialogContent data-testid="webauthn-step-up-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5" aria-hidden="true" />
            {t('security.stepUpTitle', 'Confirm this security-sensitive action')}
          </DialogTitle>
          <DialogDescription>
            {t(
              'security.stepUpDescription',
              'Use a passkey or security key. Verification is bound to this site and remains valid for 10 minutes.',
            )}
          </DialogDescription>
        </DialogHeader>

        {mode === 'loading' ? (
          <p className="py-4 text-sm text-muted-foreground" role="status">
            {t('security.checkingPasskeys', 'Checking registered passkeys…')}
          </p>
        ) : null}

        {mode === 'authenticate' ? (
          <div className="space-y-3 py-2">
            <div className="flex items-start gap-3 rounded-md border border-edge-subtle bg-muted/30 p-3">
              <KeyRound className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <p className="text-sm text-muted-foreground">
                {t(
                  'security.passkeyPrompt',
                  'Your browser will ask for the device PIN, biometric check, or security key associated with this account.',
                )}
              </p>
            </div>
          </div>
        ) : null}

        {mode === 'enroll' ? (
          <div className="space-y-4 py-2">
            <p className="text-sm text-muted-foreground">
              {t(
                'security.passkeyRequired',
                'No passkey is registered yet. Confirm your password to add one before continuing.',
              )}
            </p>
            <div className="grid gap-2">
              <Label htmlFor="step-up-password">
                {t('security.currentPassword', 'Current password')}
              </Label>
              <Input
                id="step-up-password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="step-up-credential-name">
                {t('security.passkeyName', 'Passkey name')}
              </Label>
              <Input
                id="step-up-credential-name"
                value={credentialName}
                maxLength={80}
                onChange={(event) => setCredentialName(event.target.value)}
              />
            </div>
          </div>
        ) : null}

        {error ? (
          <p className="text-sm text-destructive" role="alert">{error}</p>
        ) : null}

        <DialogFooter>
          <Button type="button" variant="outline" disabled={busy} onClick={() => finish(false)}>
            {t('common_cancel', 'Cancel')}
          </Button>
          {mode === 'authenticate' ? (
            <Button type="button" disabled={busy} onClick={() => void authenticate()}>
              {busy
                ? t('security.verifying', 'Verifying…')
                : t('security.usePasskey', 'Use passkey')}
            </Button>
          ) : null}
          {mode === 'enroll' ? (
            <Button type="button" disabled={busy || !password} onClick={() => void enroll()}>
              {busy
                ? t('security.addingPasskey', 'Adding…')
                : t('security.addPasskeyContinue', 'Add passkey and continue')}
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
