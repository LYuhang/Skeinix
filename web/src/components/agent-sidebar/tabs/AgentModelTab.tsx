/**
 * AgentModelTab — presentational "Model" tab of the Agent settings modal.
 *
 * Renders advanced hyperparameter inputs (temperature / max_tokens / timeout).
 * Runtime model and effort are live per-turn controls in the composer. All state
 * + setters are owned by the modal and passed in; this component never touches
 * the store. The ids/testids match the legacy modal contract exactly.
 */
import { useTranslation } from 'react-i18next';

import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
export interface AgentModelTabProps {
  temperature: string;
  setTemperature: (v: string) => void;
  maxTokens: string;
  setMaxTokens: (v: string) => void;
  timeout: string;
  setTimeout: (v: string) => void;
}

export function AgentModelTab({
  temperature,
  setTemperature,
  maxTokens,
  setMaxTokens,
  timeout,
  setTimeout,
}: AgentModelTabProps) {
  const { t } = useTranslation();

  return (
    <div className="space-y-5">
      {/* ----- Hyperparameters ----- */}
      <div className="space-y-1">
        <Label htmlFor="agent-settings-temperature">
          {t('agent_settings.temperature', 'Temperature')}
        </Label>
        <Input
          id="agent-settings-temperature"
          data-testid="agent-settings-temperature"
          type="number"
          min={0}
          max={2}
          step={0.1}
          placeholder={t('agent_settings.default_ph', 'Default')}
          value={temperature}
          onChange={(e) => setTemperature(e.target.value)}
        />
      </div>

      <div className="space-y-1">
        <Label htmlFor="agent-settings-max-tokens">
          {t('agent_settings.max_tokens', 'Max tokens')}
        </Label>
        <Input
          id="agent-settings-max-tokens"
          data-testid="agent-settings-max-tokens"
          type="number"
          min={1}
          step={1}
          placeholder={t('agent_settings.default_ph', 'Default')}
          value={maxTokens}
          onChange={(e) => setMaxTokens(e.target.value)}
        />
      </div>

      <div className="space-y-1">
        <Label htmlFor="agent-settings-timeout">
          {t('agent_settings.timeout', 'Timeout (seconds)')}
        </Label>
        <Input
          id="agent-settings-timeout"
          data-testid="agent-settings-timeout"
          type="number"
          min={1}
          step={1}
          placeholder={t('agent_settings.default_ph', 'Default')}
          value={timeout}
          onChange={(e) => setTimeout(e.target.value)}
        />
      </div>
    </div>
  );
}
