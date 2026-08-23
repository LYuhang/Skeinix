import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { KeyRound, Plus, Trash2 } from 'lucide-react';
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
import { createWebAuthnCredential } from '@/lib/auth/webauthn-browser';
import {
  beginWebAuthnRegistration,
  deleteWebAuthnCredential,
  finishWebAuthnRegistration,
  getWebAuthnStatus,
  type WebAuthnCredentialSummary,
} from '@/lib/api/passkeys';
import { useFormatDateTime } from '@/lib/timezone';

function errorMessage(reason: unknown, cancelled: string): string {
  if (reason instanceof DOMException && reason.name === 'NotAllowedError') {
    return cancelled;
  }
  return reason instanceof Error ? reason.message : String(reason);
}

export function PasskeySecuritySection() {
  const { t } = useTranslation();
  const formatDateTime = useFormatDateTime();
  const [loading, setLoading] = useState(true);
  const [passkeys, setPasskeys] = useState<WebAuthnCredentialSummary[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const [password, setPassword] = useState('');
  const [name, setName] = useState(() => t('security.defaultPasskeyName', 'Passkey'));
  const [deleteTarget, setDeleteTarget] = useState<WebAuthnCredentialSummary | null>(null);
  const [deletePassword, setDeletePassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const status = await getWebAuthnStatus();
      setPasskeys(status.credentials);
    } catch (reason) {
      setError(errorMessage(reason, t('security.passkeyCancelled', 'Passkey creation was cancelled or timed out.')));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    let active = true;
    void getWebAuthnStatus()
      .then((status) => {
        if (active) setPasskeys(status.credentials);
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(errorMessage(reason, t('security.passkeyCancelled', 'Passkey creation was cancelled or timed out.')));
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [t]);

  const addPasskey = async () => {
    if (!password) return;
    setBusy(true);
    setError('');
    try {
      const options = await beginWebAuthnRegistration(password);
      const credential = await createWebAuthnCredential(options);
      await finishWebAuthnRegistration(
        credential,
        name.trim() || t('security.defaultPasskeyName', 'Passkey'),
      );
      setAddOpen(false);
      setPassword('');
      toast.success(t('security.passkeyAdded', 'Passkey added'));
      await load();
    } catch (reason) {
      setError(errorMessage(reason, t('security.passkeyCancelled', 'Passkey creation was cancelled or timed out.')));
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
      setError(errorMessage(reason, t('security.passkeyCancelled', 'Passkey creation was cancelled or timed out.')));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="border-b border-edge-subtle py-5" data-testid="passkey-security-section">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 max-w-2xl">
          <h3 className="flex items-center gap-2 text-sm font-medium">
            <KeyRound className="h-4 w-4" aria-hidden="true" />
            {t('security.passkeys', 'Passkeys and security keys')}
          </h3>
          <p className="mt-1 text-sm text-muted-foreground">
            {t('security.passkeysDescription', 'Required for sharing, credentials, organization administration, and other high-risk changes.')}
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={() => { setError(''); setAddOpen(true); }}>
          <Plus className="mr-1.5 h-4 w-4" aria-hidden="true" />
          {t('security.addPasskey', 'Add passkey')}
        </Button>
      </div>

      {loading ? (
        <p className="mt-4 text-sm text-muted-foreground" role="status">
          {t('security.loadingPasskeys', 'Loading passkeys…')}
        </p>
      ) : passkeys.length ? (
        <div className="mt-4 rounded-md border border-edge-subtle">
          {passkeys.map((credential) => (
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
          ))}
        </div>
      ) : (
        <p className="mt-4 rounded-md border border-edge-subtle p-4 text-sm text-muted-foreground">
          {t('security.noPasskeys', 'No passkeys registered. A passkey is required before a high-risk action can continue.')}
        </p>
      )}

      {error && !addOpen && !deleteTarget ? (
        <p className="mt-3 text-sm text-destructive" role="alert">{error}</p>
      ) : null}

      <Dialog open={addOpen} onOpenChange={(next) => { if (!busy) setAddOpen(next); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('security.addPasskey', 'Add passkey')}</DialogTitle>
            <DialogDescription>{t('security.addPasskeyDescription', 'Confirm your password, then follow the secure browser prompt.')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid gap-2">
              <Label htmlFor="passkey-name">{t('security.passkeyName', 'Passkey name')}</Label>
              <Input id="passkey-name" maxLength={80} value={name} onChange={(event) => setName(event.target.value)} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="passkey-password">{t('security.currentPassword', 'Current password')}</Label>
              <Input id="passkey-password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} />
            </div>
            {error ? <p className="text-sm text-destructive" role="alert">{error}</p> : null}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={busy} onClick={() => setAddOpen(false)}>{t('common_cancel', 'Cancel')}</Button>
            <Button disabled={busy || !password} onClick={() => void addPasskey()}>{busy ? t('security.addingPasskey', 'Adding…') : t('security.addPasskey', 'Add passkey')}</Button>
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
    </section>
  );
}
