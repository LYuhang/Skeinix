import { useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  discoverOrganizationSso,
  enterpriseSsoStartUrl,
  EnterpriseIdentityApiError,
  type EnterpriseSsoProvider,
} from '@/lib/api/enterprise-identity';

export function EnterpriseSsoLogin() {
  const { t } = useTranslation();
  const [organizationSlug, setOrganizationSlug] = useState('');
  const [providers, setProviders] = useState<EnterpriseSsoProvider[] | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const slug = organizationSlug.trim();
    if (!slug) {
      setError(t('auth_sso_slug_required', 'Enter your organization slug.'));
      return;
    }
    setPending(true);
    setError(null);
    setProviders(null);
    try {
      setProviders(await discoverOrganizationSso(slug));
    } catch (caught) {
      setError(caught instanceof EnterpriseIdentityApiError && caught.status === 429
        ? t('auth_sso_rate_limited', 'Too many attempts. Try again later.')
        : t('auth_sso_discovery_error', 'Unable to find company sign-in options.'));
    } finally {
      setPending(false);
    }
  };

  return (
    <section aria-label={t('auth_sso_title', 'Company SSO')}>
      <p className="mb-4 text-sm leading-6 text-muted-foreground">
        {t('auth_sso_description', 'Use the organization slug provided by your administrator.')}
      </p>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <div className="flex flex-col gap-2">
          <Label htmlFor="sso-organization-slug">
            {t('auth_sso_slug', 'Organization slug')}
          </Label>
          <Input
            id="sso-organization-slug"
            value={organizationSlug}
            onChange={(event) => {
              setOrganizationSlug(event.target.value);
              setProviders(null);
              setError(null);
            }}
            autoComplete="organization"
            maxLength={160}
            placeholder={t('auth_sso_slug_placeholder', 'your-company')}
          />
        </div>
        <Button type="submit" disabled={pending} aria-busy={pending}>
          {pending
            ? t('auth_sso_finding', 'Finding sign-in options…')
            : t('auth_sso_continue', 'Continue')}
        </Button>
      </form>
      <div className="mt-3 flex flex-col gap-2" aria-live="polite">
        {error ? <p role="alert" className="text-xs text-destructive">{error}</p> : null}
        {providers?.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            {t('auth_sso_none', 'No company sign-in option was found. Check the slug or contact your administrator.')}
          </p>
        ) : null}
        {providers?.map((provider) => (
          <Button key={provider.provider_id} asChild variant="outline" className="w-full">
            <a href={enterpriseSsoStartUrl(provider.provider_id)}>
              {t('auth_sso_use_provider', 'Continue with {{provider}}', {
                provider: provider.display_name,
              })}
            </a>
          </Button>
        ))}
      </div>
    </section>
  );
}
