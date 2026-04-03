from __future__ import annotations

import truststore

# Inject system certificates into ssl before the frozen app starts.
truststore.inject_into_ssl()
