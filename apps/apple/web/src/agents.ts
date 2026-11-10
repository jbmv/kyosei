// How a subagent is named in the status bar and the side panel (KYO-726).

/** The family word of a model id: `claude-fable-5-2` is `fable`, `claude-3-5-sonnet-20241022` is `sonnet`. */
export function shortModel(model: string | null | undefined): string {
  if (!model) return "";
  const family = model.split("-").find((part) => /^[a-z]+$/.test(part) && part !== "claude");
  return family ?? model;
}

/**
 * `fable medium`: the model's family word and the effort, either left out when the
 * transcript records none. A subagent that has not replied yet has neither, and is named by
 * its type until it has.
 */
export function agentKind(
  agent: { model?: string | null; effort?: string | null; agent_type?: string | null },
): string {
  const words = [shortModel(agent.model), agent.effort ?? ""].filter(Boolean).join(" ");
  return words || agent.agent_type || "";
}
