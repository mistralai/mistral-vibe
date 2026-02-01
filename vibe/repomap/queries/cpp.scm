; C++ symbol extraction

(function_definition
  declarator: (function_declarator
    declarator: (identifier) @name.definition.function)) @definition.function

(function_definition
  declarator: (function_declarator
    declarator: (qualified_identifier
      name: (identifier) @name.definition.function))) @definition.function

(declaration
  declarator: (function_declarator
    declarator: (identifier) @name.definition.function)) @definition.function

(class_specifier
  name: (type_identifier) @name.definition.class) @definition.class

(struct_specifier
  name: (type_identifier) @name.definition.class) @definition.class

(enum_specifier
  name: (type_identifier) @name.definition.class) @definition.class

(namespace_definition
  name: (identifier) @name.definition.namespace) @definition.namespace

(type_definition
  declarator: (type_identifier) @name.definition.type) @definition.type

(template_declaration
  (class_specifier
    name: (type_identifier) @name.definition.class)) @definition.class

(template_declaration
  (function_definition
    declarator: (function_declarator
      declarator: (identifier) @name.definition.function))) @definition.function

(preproc_def
  name: (identifier) @name.definition.macro) @definition.macro

(preproc_function_def
  name: (identifier) @name.definition.macro) @definition.macro

(call_expression
  function: (identifier) @name.reference.call) @reference.call

(call_expression
  function: (field_expression
    field: (field_identifier) @name.reference.call)) @reference.call

(call_expression
  function: (qualified_identifier
    name: (identifier) @name.reference.call)) @reference.call

(preproc_include
  path: (string_literal) @name.reference.import) @reference.import

(preproc_include
  path: (system_lib_string) @name.reference.import) @reference.import

(using_declaration
  (qualified_identifier) @name.reference.import) @reference.import
