/**
 * Re-exports of the pinned wire types this feature consumes — components
 * import from the feature, never straight from the client, so a contract
 * change surfaces here first (pattern from the employees feature).
 */

export type { ListUsersParams, Role, User } from "@workpulse/api-client/index.schemas";
