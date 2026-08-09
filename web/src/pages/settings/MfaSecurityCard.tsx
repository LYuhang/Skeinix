import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { KeyRound, Plus, ShieldCheck, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
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
  createWebAuthnCredential,
} from '@/lib/auth/webauthn-browser';
import {
  beginTotpEnrollment,
  beginWebAuthnRegistration,
  confirmTotpEnrollment,
  deleteWebAuthnCredential,
  disableTotp,
  finishWebAuthnRegistration,
  getTotpStatus,
  getWebAuthnStatus,
  type TotpEnrollment,
  type WebAuthnCredentialSummary,
} from '@/lib/api/mfa';
import { useFormatDateTime } from '@/lib/timezone';

function errorMessage(reason: unknown): string {
  if (reason instanceof DOMException && reason.name === 'NotAllowedError') {
    return 'Passkey creation was cancelled or timed out.';
  }
  return reason instanceof Error ? reason.message : String(reason);
}

async function fetchMfaStatus() {
  const [webauthn, totp] = await Promise.all([
    getWebAuthnStatus(),
    getTotpStatus(),
  ]);
  return { passkeys: webauthn.credentials, totpEnabled: totp.enabled };
}

export function MfaSecurityCard() {
  const { t } = useTranslation();
  const formatDateTime = useFormatDateTime();
  const [loading, setLoading] = useState(true);
  const [passkeys, setPasskeys] = useState<WebAuthnCredentialSummary[]>([]);
  const [totpEnabled, setTotpEnabled] = useState(false);
  const [addPasskeyOpen, setAddPasskeyOpen] = useState(false);
  const [passkeyPassword, setPasskeyPassword] = useState('');
  const [passkeyName, setPasskeyName] = useState('Passkey');
  const [deleteTarget, setDeleteTarget] = useState<WebAuthnCredentialSummary | null>(null);
  const [deletePassword, setDeletePassword] = useState('');
  const [totpOpen, setTotpOpen] = useState(false);
  const [totpPassword, setTotpPassword] = useState('');
  const [totpEnrollment, setTotpEnrollment] = useState<TotpEnrollment | null>(null);
  const [totpCode, setTotpCode] = useState('');
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [disableTotpOpen, setDisableTotpOpen] = useState(false);
  const [disablePassword, setDisablePassword] = useState('');
  const [disableCode, setDisableCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const status = await fetchMfaStatus();
      setPasskeys(status.passkeys);
      setTotpEnabled(status.totpEnabled);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    void fetchMfaStatus()
      .then((status) => {
        if (!active) return;
        setPasskeys(status.passkeys);
        setTotpEnabled(status.totpEnabled);
      })
      .catch((reason: unknown) => {
        if (active) setError(errorMessage(reason));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const addPasskey = async () => {
    if (!passkeyPassword) return;
    setBusy(true);
    setError('');
    try {
      const options = await beginWebAuthnRegistration(passkeyPassword);
      const credential = await createWebAuthnCredential(options);
      await finishWebAuthnRegistration(
        credential,
        passkeyName.trim() || 'Passkey',
      );
      setAddPasskeyOpen(false);
      setPasskeyPassword('');
      toast.success(t('security.passkeyAdded', 'Passkey added'));
      await load();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  const removePasskey = async () => {
    if (!deleteTarget || !deletePassword) return;
    setBusy(true);
    setError('');
    try {
      await deleteWebAuthnCredential(deleteTarget.credential_id, deletePassword);
      setDeleteTarget(null);
      setDeletePassword('');
      toast.success(t('security.passkeyRemoved', 'Passkey removed'));
      await load();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  const startTotp = async () => {
    if (!totpPassword) return;
    setBusy(true);
    setError('');
    try {
      setTotpEnrollment(await beginTotpEnrollment(totpPassword));
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  const confirmTotp = async () => {
    setBusy(true);
    setError('');
    try {
      const result = await confirmTotpEnrollment(totpCode);
      setRecoveryCodes(result.recovery_codes);
      setTotpEnabled(true);
      toast.success(t('security.totpEnabled', 'Authenticator app enabled'));
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  const closeTotp = () => {
    setTotpOpen(false);
    setTotpPassword('');
    setTotpEnrollment(null);
    setTotpCode('');
    setRecoveryCodes([]);
    setError('');
  };

  const turnOffTotp = async () => {
    if (!disablePassword || !disableCode) return;
    setBusy(true);
    setError('');
    try {
      await disableTotp(disablePassword, disableCode);
      setTotpEnabled(false);
      setDisableTotpOpen(false);
      setDisablePassword('');
      setDisableCode('');
      toast.success(t('security.totpDisabled', 'Authenticator app disabled'));
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="border-b border-edge-subtle py-5" data-testid="mfa-security-card">
      <div className="mb-5">
        <h3 className="flex items-center gap-2 text-sm font-medium">
          <ShieldCheck className="h-4 w-4" aria-hidden="true" />
          {t('security.mfaTitle', 'Multi-factor authentication')}
        </h3>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          {t(
            'security.mfaDescription',
            'Passkeys protect security-sensitive administration. Authenticator codes are available as an additional account factor and recovery option.',
          )}
        </p>
      </div>

      {loading ? (
        <p className="text-sm text-muted-foreground" role="status">
          {t('security.loadingFactors', 'Loading security factors…')}
        </p>
      ) : (
        <div className="space-y-5">
          <div className="rounded-md border border-edge-subtle">
            <div className="flex items-center justify-between gap-4 border-b border-edge-subtle p-4">
              <div>
                <h4 className="text-sm font-medium">{t('security.passkeys', 'Passkeys and security keys')}</h4>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {t('security.passkeysDescription', 'Required for sharing, credentials, organization administration, and other high-risk changes.')}
                </p>
              </div>
              <Button size="sm" variant="outline" onClick={() => { setError(''); setAddPasskeyOpen(true); }}>
                <Plus className="mr-1.5 h-4 w-4" aria-hidden="true" />
                {t('security.addPasskey', 'Add passkey')}
              </Button>
            </div>
            {passkeys.length ? passkeys.map((credential) => (
              <div key={credential.credential_id} className="flex items-center gap-3 border-b border-edge-subtle p-4 last:border-b-0">
                <KeyRound className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{credential.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {credential.backed_up
                      ? t('security.syncedPasskey', 'Synced passkey')
                      : t('security.devicePasskey', 'Device-bound credential')}
                    {' · '}{formatDateTime(credential.created_at)}
                  </p>
                </div>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  aria-label={t('security.removePasskey', 'Remove passkey')}
                  onClick={() => { setError(''); setDeleteTarget(credential); }}
                >
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                </Button>
              </div>
            )) : (
              <p className="p-4 text-sm text-muted-foreground">
                {t('security.noPasskeys', 'No passkeys registered. A passkey is required before a high-risk action can continue.')}
              </p>
            )}
          </div>

          <div className="flex items-center justify-between gap-4 rounded-md border border-edge-subtle p-4">
            <div>
              <h4 className="text-sm font-medium">{t('security.authenticatorApp', 'Authenticator app')}</h4>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {totpEnabled
                  ? t('security.authenticatorEnabled', 'Enabled. Codes do not replace passkey verification for privileged actions.')
                  : t('security.authenticatorDisabled', 'Optional time-based codes and one-time recovery codes.')}
              </p>
            </div>
            {totpEnabled ? (
              <Button size="sm" variant="outline" onClick={() => { setError(''); setDisableTotpOpen(true); }}>
                {t('security.disable', 'Disable')}
              </Button>
            ) : (
              <Button size="sm" variant="outline" onClick={() => { setError(''); setTotpOpen(true); }}>
                {t('security.setUp', 'Set up')}
              </Button>
            )}
          </div>
        </div>
      )}

      {error && !addPasskeyOpen && !deleteTarget && !totpOpen && !disableTotpOpen ? (
        <p className="mt-3 text-sm text-destructive" role="alert">{error}</p>
      ) : null}

      <Dialog open={addPasskeyOpen} onOpenChange={(next) => { if (!busy) setAddPasskeyOpen(next); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('security.addPasskey', 'Add passkey')}</DialogTitle>
            <DialogDescription>{t('security.addPasskeyDescription', 'Confirm your password, then follow the secure browser prompt.')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid gap-2">
              <Label htmlFor="passkey-name">{t('security.passkeyName', 'Passkey name')}</Label>
              <Input id="passkey-name" maxLength={80} value={passkeyName} onChange={(event) => setPasskeyName(event.target.value)} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="passkey-password">{t('security.currentPassword', 'Current password')}</Label>
              <Input id="passkey-password" type="password" autoComplete="current-password" value={passkeyPassword} onChange={(event) => setPasskeyPassword(event.target.value)} />
            </div>
            {error ? <p className="text-sm text-destructive" role="alert">{error}</p> : null}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={busy} onClick={() => setAddPasskeyOpen(false)}>{t('common_cancel', 'Cancel')}</Button>
            <Button disabled={busy || !passkeyPassword} onClick={() => void addPasskey()}>{busy ? t('security.addingPasskey', 'Adding…') : t('security.addPasskey', 'Add passkey')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={deleteTarget !== null} onOpenChange={(next) => { if (!next && !busy) setDeleteTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('security.removePasskey', 'Remove passkey')}</DialogTitle>
            <DialogDescription>{t('security.removePasskeyDescription', 'Verify with a registered passkey and confirm your password. Other sessions are not elevated by this action.')}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="remove-passkey-password">{t('security.currentPassword', 'Current password')}</Label>
            <Input id="remove-passkey-password" type="password" autoComplete="current-password" value={deletePassword} onChange={(event) => setDeletePassword(event.target.value)} />
            {error ? <p className="text-sm text-destructive" role="alert">{error}</p> : null}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={busy} onClick={() => setDeleteTarget(null)}>{t('common_cancel', 'Cancel')}</Button>
            <Button variant="destructive" disabled={busy || !deletePassword} onClick={() => void removePasskey()}>{t('security.remove', 'Remove')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={totpOpen} onOpenChange={(next) => { if (!next && !busy) closeTotp(); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('security.setupAuthenticator', 'Set up authenticator app')}</DialogTitle>
            <DialogDescription>{t('security.totpNotPrivileged', 'Authenticator codes are not phishing-resistant and do not authorize privileged changes.')}</DialogDescription>
          </DialogHeader>
          {!totpEnrollment ? (
            <div className="grid gap-2">
              <Label htmlFor="totp-password">{t('security.currentPassword', 'Current password')}</Label>
              <Input id="totp-password" type="password" autoComplete="current-password" value={totpPassword} onChange={(event) => setTotpPassword(event.target.value)} />
            </div>
          ) : recoveryCodes.length === 0 ? (
            <div className="space-y-4">
              <div className="rounded-md bg-muted p-3 font-mono text-sm break-all" data-testid="totp-secret">{totpEnrollment.secret}</div>
              <p className="text-xs text-muted-foreground">{t('security.totpSecretHelp', 'Enter this setup key in your authenticator app, then enter its six-digit code.')}</p>
              <div className="grid gap-2">
                <Label htmlFor="totp-code">{t('security.verificationCode', 'Verification code')}</Label>
                <Input id="totp-code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={totpCode} onChange={(event) => setTotpCode(event.target.value.replace(/\D/g, ''))} />
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-sm font-medium">{t('security.saveRecoveryCodes', 'Save these recovery codes now')}</p>
              <p className="text-xs text-muted-foreground">{t('security.recoveryCodesOnce', 'Each code works once. They will not be shown again.')}</p>
              <div className="grid grid-cols-2 gap-2 rounded-md bg-muted p-3 font-mono text-xs" data-testid="mfa-recovery-codes">
                {recoveryCodes.map((code) => <span key={code}>{code}</span>)}
              </div>
              <Button type="button" variant="outline" size="sm" onClick={() => void navigator.clipboard.writeText(recoveryCodes.join('\n'))}>
                {t('security.copyRecoveryCodes', 'Copy recovery codes')}
              </Button>
            </div>
          )}
          {error ? <p className="text-sm text-destructive" role="alert">{error}</p> : null}
          <DialogFooter>
            {recoveryCodes.length ? (
              <Button onClick={closeTotp}>{t('common_done', 'Done')}</Button>
            ) : (
              <>
                <Button variant="outline" disabled={busy} onClick={closeTotp}>{t('common_cancel', 'Cancel')}</Button>
                {!totpEnrollment ? (
                  <Button disabled={busy || !totpPassword} onClick={() => void startTotp()}>{t('common_continue', 'Continue')}</Button>
                ) : (
                  <Button disabled={busy || totpCode.length !== 6} onClick={() => void confirmTotp()}>{t('security.verifyEnable', 'Verify and enable')}</Button>
                )}
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={disableTotpOpen} onOpenChange={(next) => { if (!busy) setDisableTotpOpen(next); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('security.disableAuthenticator', 'Disable authenticator app')}</DialogTitle>
            <DialogDescription>{t('security.disableAuthenticatorDescription', 'Confirm your password and provide a fresh authenticator or recovery code.')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid gap-2">
              <Label htmlFor="disable-totp-password">{t('security.currentPassword', 'Current password')}</Label>
              <Input id="disable-totp-password" type="password" autoComplete="current-password" value={disablePassword} onChange={(event) => setDisablePassword(event.target.value)} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="disable-totp-code">{t('security.verificationOrRecoveryCode', 'Authenticator or recovery code')}</Label>
              <Input id="disable-totp-code" autoComplete="one-time-code" value={disableCode} onChange={(event) => setDisableCode(event.target.value)} />
            </div>
            {error ? <p className="text-sm text-destructive" role="alert">{error}</p> : null}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={busy} onClick={() => setDisableTotpOpen(false)}>{t('common_cancel', 'Cancel')}</Button>
            <Button variant="destructive" disabled={busy || !disablePassword || !disableCode} onClick={() => void turnOffTotp()}>{t('security.disable', 'Disable')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
