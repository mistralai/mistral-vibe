; Perl symbol extraction

(subroutine_declaration
  name: (bareword) @name.definition.function) @definition.function

(function_definition
  name: (bareword) @name.definition.function) @definition.function

(package_statement
  (package) @name.definition.class) @definition.class

(use_statement
  (package) @name.reference.import) @reference.import

(require_statement
  (package) @name.reference.import) @reference.import

(method_call
  name: (bareword) @name.reference.call) @reference.call

(function_call
  name: (bareword) @name.reference.call) @reference.call

(scalar_variable
  (varname) @name.definition.property) @definition.property

(hash_variable
  (varname) @name.definition.property) @definition.property

(array_variable
  (varname) @name.definition.property) @definition.property
