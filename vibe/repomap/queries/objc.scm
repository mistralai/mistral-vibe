; Objective-C symbol extraction

(class_interface
  name: (identifier) @name.definition.class) @definition.class

(class_implementation
  name: (identifier) @name.definition.class) @definition.class

(category_interface
  name: (identifier) @name.definition.class) @definition.class

(category_implementation
  name: (identifier) @name.definition.class) @definition.class

(protocol_declaration
  name: (identifier) @name.definition.interface) @definition.interface

(method_declaration
  selector: (identifier) @name.definition.function) @definition.function

(method_definition
  selector: (identifier) @name.definition.function) @definition.function

(property_declaration
  declarator: (identifier) @name.definition.property) @definition.property

(function_definition
  declarator: (function_declarator
    declarator: (identifier) @name.definition.function)) @definition.function

(message_expression
  selector: (identifier) @name.reference.call) @reference.call

(preproc_import
  path: (string_literal) @name.reference.import) @reference.import

(superclass_reference
  (identifier) @name.reference.inherit) @reference.inherit
