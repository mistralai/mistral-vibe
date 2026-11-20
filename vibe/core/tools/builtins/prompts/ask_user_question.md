Ask the user questions with structured options and wait for their answers. Put every blocking decision in this call, one question per decision. A decision is blocking when only the user can settle it (not the request, the code, or the project's conventions) and its answer changes what you do next; settle the rest yourself.

The user may not have read your tool output, so make each question self-contained: name the items it is about and why you ask, never "these three" or "the above". After tool calls, first say in a sentence or two what you found, then ask.
