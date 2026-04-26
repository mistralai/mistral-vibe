#!/usr/bin/env python3

import asyncio
from pathlib import Path
from vibe.core.plugins.builtin.lsp.lsp_client import LspClient
from vibe.core.plugins.builtin.lsp.registry import LSP_REGISTRY

async def test_lsp():
    print("=== LSP Fix Test ===")
    
    # Check configuration
    config = LSP_REGISTRY['python']
    print(f"Config: {config}")
    print(f"Is available: {config.is_available()}")
    print(f"Command: {config.command}")
    
    # Try to start LSP client
    root = Path.cwd()
    client = LspClient(config, root)
    
    try:
        print('\nStarting LSP...')
        await client.start()
        print('LSP started successfully!')
        print(f'Is running: {client.is_running}')
        
        # Test document symbols
        print('\nTesting document symbols...')
        symbols = await client.document_symbols('test_file.py')
        print(f'Symbols found: {len(symbols) if symbols else 0}')
        if symbols:
            for symbol in symbols:
                print(f"  - {symbol.get("name", "?")} ({symbol.get("kind", "?")})")
        else:
            print("  No symbols found (this might be expected for a simple file)")
        
        print('\nTest completed successfully!')
        return True
        
    except Exception as e:
        print(f'Error: {e}')
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Try to stop the client gracefully
        try:
            await client.stop()
            print('LSP stopped')
        except Exception as e:
            print(f'Error stopping LSP (this might be expected): {e}')

if __name__ == "__main__":
    success = asyncio.run(test_lsp())
    print(f"\nTest result: {'SUCCESS' if success else 'FAILED'}")
    exit(0 if success else 1)