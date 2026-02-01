; PHP symbol extraction

(class_declaration
  name: (name) @name.definition.class) @definition.class

(interface_declaration
  name: (name) @name.definition.interface) @definition.interface

(trait_declaration
  name: (name) @name.definition.class) @definition.class

(enum_declaration
  name: (name) @name.definition.class) @definition.class

(function_definition
  name: (name) @name.definition.function) @definition.function

(method_declaration
  name: (name) @name.definition.function) @definition.function

(const_declaration
  (const_element
    name: (name) @name.definition.constant)) @definition.constant

(function_call_expression
  function: (name) @name.reference.call) @reference.call

(function_call_expression
  function: (qualified_name
    (name) @name.reference.call)) @reference.call

(member_call_expression
  name: (name) @name.reference.call) @reference.call

(scoped_call_expression
  name: (name) @name.reference.call) @reference.call

(object_creation_expression
  (name) @name.reference.call) @reference.call

(namespace_use_declaration
  (namespace_use_clause
    (qualified_name) @name.reference.import)) @reference.import

(namespace_definition
  name: (namespace_name) @name.definition.namespace) @definition.namespace

(base_clause
  (name) @name.reference.inherit) @reference.inherit

(class_interface_clause
  (name) @name.reference.inherit) @reference.inherit
