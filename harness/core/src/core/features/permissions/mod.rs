mod approval;
pub(crate) use approval::{ApprovalFailureReason, ApprovalOutcome};
mod grant_key;
pub(crate) use grant_key::GrantKey;
mod policy;
pub(crate) use policy::{PermissionDecision, PermissionPolicy};
mod public_arguments;
pub(crate) use public_arguments::PublicArguments;

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum PermissionResolution {
    Allow,
    Deny,
    Ask(GrantKey),
}
