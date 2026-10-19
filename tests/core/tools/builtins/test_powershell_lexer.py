from __future__ import annotations

import pytest

from vibe.core.tools.builtins._powershell_lexer import (
    powershell_redirection_targets,
    split_powershell_arguments,
    split_powershell_commands,
)


def test_splits_single_ampersand_outside_quotes():
    assert split_powershell_commands("dir & del secrets.txt") == [
        "dir",
        "del secrets.txt",
    ]
    assert split_powershell_commands('echo "a & b" & dir') == ['echo "a & b"', "dir"]


def test_splits_newlines_outside_quotes():
    assert split_powershell_commands("dir\ndel secrets.txt") == [
        "dir",
        "del secrets.txt",
    ]
    assert split_powershell_commands('echo "a\nb"\ndir') == ['echo "a\nb"', "dir"]


def test_extracts_nested_commands():
    assert split_powershell_commands("echo $(Get-Date; Remove-Item secret.txt)") == [
        "echo $(Get-Date; Remove-Item secret.txt)",
        "Get-Date",
        "Remove-Item secret.txt",
    ]
    assert split_powershell_commands('echo "prefix $(Get-Date)"; Get-Location') == [
        'echo "prefix $(Get-Date)"',
        "Get-Location",
        "Get-Date",
    ]
    assert split_powershell_commands("echo '$(Remove-Item secret.txt)'") == [
        "echo '$(Remove-Item secret.txt)'"
    ]
    assert split_powershell_commands("& { Get-Date; cmd /k }") == [
        "{ Get-Date; cmd /k }",
        "Get-Date",
        "cmd /k",
    ]
    assert split_powershell_commands("& (Get-Date; cmd /k)") == [
        "(Get-Date; cmd /k)",
        "Get-Date",
        "cmd /k",
    ]


def test_hashtable_values_are_nested_commands():
    assert split_powershell_commands("echo @{a = Remove-Item x}") == [
        "echo @{a = Remove-Item x}",
        "a = Remove-Item x",
    ]


def test_double_quoted_here_string_subexpressions_are_nested_commands():
    assert split_powershell_commands('echo @"\n$(Remove-Item x)\n"@') == [
        'echo @"\n$(Remove-Item x)\n"@',
        "Remove-Item x",
    ]


@pytest.mark.parametrize(
    "command",
    [
        # A backtick is literal inside single quotes.
        "echo 'a`'; Remove-Item x",
        # "^" is cmd.exe's escape character, not PowerShell's.
        "echo a^; Remove-Item x",
        # Any single-quote character closes a single-quoted string.
        "echo 'x’; Remove-Item x; ‘y'",
        "echo 'a'#it's\nRemove-Item x",
        "echo hi # it's\nRemove-Item x",
        "echo <# it's #> ; Remove-Item x",
        "echo @'\nit's\n'@; Remove-Item x",
        "cmd --% /c it's | Remove-Item x",
        # Stop-parsing is decided on the decoded word.
        "echo '--%' it's | Remove-Item x",
        "echo -`-% it's | Remove-Item x",
        # The word after "-name:" is that parameter's value, even "--%".
        "echo -a:--%; Remove-Item x",
        "echo -a: --%; Remove-Item x",
        # The word after "," is one more array element, even "--%".
        "echo a, --% b; Remove-Item x",
        "echo a,'--%' b; Remove-Item x",
        "echo a, <# c #> --% b; Remove-Item x",
        "echo a,\n--% b; Remove-Item x",
        "echo a, b --% it's | Remove-Item x",
        # Windows PowerShell 5.1 has no `u{} escape, so this is not "--%" there.
        "echo `u{2D}`u{2D}% x; Remove-Item x",
        # PowerShell keeps U+001C-U+001F inside a word; Python calls them spaces.
        "echo a\x1c#; Remove-Item x",
        "echo `\x1f#; Remove-Item x",
        # A ")" in a quote or comment does not close a subexpression.
        'echo x$(echo ")"; Remove-Item x)',
        "echo x$(<# ) #> Remove-Item x)",
        "echo \"$(echo ')'; Remove-Item x)\"",
        'echo @"\n$(echo ")"; Remove-Item x)\n"@',
    ],
)
def test_no_statement_hides_inside_another(command):
    assert "Remove-Item x" in split_powershell_commands(command)


def test_trailing_at_sign_is_a_plain_character():
    assert split_powershell_commands("echo @") == ["echo @"]
    assert split_powershell_arguments("echo @") == ["echo", "@"]
    assert powershell_redirection_targets("echo @") == []


def test_comments_are_dropped():
    assert split_powershell_commands("dir # ; Remove-Item x") == ["dir"]
    assert split_powershell_commands("dir <# ; Remove-Item x #> .") == [
        "dir <# ; Remove-Item x #> ."
    ]
    assert split_powershell_arguments("dir <# ; Remove-Item x #> .") == ["dir", "."]


def test_tokenizer_preserves_backslash_paths():
    assert split_powershell_arguments(r"type C:\Users\victim\secret.txt") == [
        "type",
        r"C:\Users\victim\secret.txt",
    ]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("type x#y C:/secret", ["type", "x#y", "C:/secret"]),
        ("type x `#y C:/secret", ["type", "x", "#y", "C:/secret"]),
        ('type `"x C:/secret `"', ["type", '"x', "C:/secret", '"']),
        ("type x,C:/secret", ["type", "x", "C:/secret"]),
        ('type "x"C:/secret', ["type", "x", "C:/secret"]),
        ("type 'x’ C:/secret '", ["type", "x", "C:/secret", ""]),
        ("type a`tb", ["type", "a\tb"]),
        ("type `u{2E}`u{2E}/secret", ["type", "../secret"]),
        ('type \'it\'\'s\' "say ""hi"""', ["type", "it's", 'say "hi"']),
        ('type a"b c"d', ["type", "ab cd"]),
        ("type `\n C:/secret", ["type", "C:/secret"]),
        ("Get-Content -Path:C:/secret", ["Get-Content", "-Path:", "C:/secret"]),
        (r"Get-Content -Path.\..\secret", ["Get-Content", "-Path", r".\..\secret"]),
        ('-a"b c"', ["-ab c"]),
        ("echo $(Get-Date) ${env:HOME}/x", ["echo", "$(Get-Date)", "${env:HOME}/x"]),
        ('cmd --% /c type "C:/a b"', ["cmd", "--%", "/c", "type", "C:/a b"]),
        ("type (echo C:/secret)", ["type", "(echo C:/secret)"]),
        ("type x,(echo C:/secret)", ["type", "x", "(echo C:/secret)"]),
        ("type a\x1cb", ["type", "a\x1cb"]),
    ],
)
def test_arguments_match_what_powershell_passes(command, expected):
    assert split_powershell_arguments(command) == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("echo pwned > output.txt", ["output.txt"]),
        ("echo pwned >> 'C:/a b.txt'", ["C:/a b.txt"]),
        ("echo pwned 2> errors.txt", ["errors.txt"]),
        ("echo pwned *>C:/out", ["C:/out"]),
        ("echo pwned>C:/out", ["C:/out"]),
        ("echo harmless 2>&1", []),
        ('echo "not > C:/out"', []),
        ("echo 'not > C:/out'", []),
        ("echo `> C:/out", []),
        ("echo pwned > (echo C:/out)", ["(echo C:/out)"]),
    ],
)
def test_redirection_targets(command, expected):
    assert powershell_redirection_targets(command) == expected
