; C language symbol extraction
; Classes don't exist in C, but we capture structs, functions, and macros

(function_definition
  declarator: (function_declarator
    declarator: (identifier) @name.definition.function)) @definition.function

(declaration
  declarator: (function_declarator
    declarator: (identifier) @name.definition.function)) @definition.function

(struct_specifier
  name: (type_identifier) @name.definition.class) @definition.class

(enum_specifier
  name: (type_identifier) @name.definition.class) @definition.class

(type_definition
  declarator: (type_identifier) @name.definition.type) @definition.type

(preproc_def
  name: (identifier) @name.definition.macro) @definition.macro

(preproc_function_def
  name: (identifier) @name.definition.macro) @definition.macro

(call_expression
  function: (identifier) @name.reference.call) @reference.call

(call_expression
  function: (field_expression
    field: (field_identifier) @name.reference.call)) @reference.call

(preproc_include
  path: (string_literal) @name.reference.import) @reference.import

(preproc_include
  path: (system_lib_string) @name.reference.import) @reference.import
