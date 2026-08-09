export interface SlashCommand {
  trigger: string;
  descKey: string;
}

/** Convert the backend-owned command catalog into composer menu entries. */
export function slashCommandsFromCatalog(names: readonly string[]): SlashCommand[] {
  return names
    .map((name) => name.trim().toLowerCase())
    .filter((name, index, values) => !!name && values.indexOf(name) === index)
    .map((name) => ({
      trigger: `/${name}`,
      descKey: `composer.cmd.${name}`,
    }));
}
