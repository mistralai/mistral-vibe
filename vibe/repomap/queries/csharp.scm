; C# symbol extraction

(class_declaration
  name: (identifier) @name.definition.class) @definition.class

(struct_declaration
  name: (identifier) @name.definition.class) @definition.class

(interface_declaration
  name: (identifier) @name.definition.interface) @definition.interface

(enum_declaration
  name: (identifier) @name.definition.class) @definition.class

(record_declaration
  name: (identifier) @name.definition.class) @definition.class

(method_declaration
  name: (identifier) @name.definition.function) @definition.function

(constructor_declaration
  name: (identifier) @name.definition.function) @definition.function

(property_declaration
  name: (identifier) @name.definition.property) @definition.property

(namespace_declaration
  name: (identifier) @name.definition.namespace) @definition.namespace

(namespace_declaration
  name: (qualified_name) @name.definition.namespace) @definition.namespace

(delegate_declaration
  name: (identifier) @name.definition.type) @definition.type

(invocation_expression
  function: (identifier) @name.reference.call) @reference.call

(invocation_expression
  function: (member_access_expression
    name: (identifier) @name.reference.call)) @reference.call

(object_creation_expression
  type: (identifier) @name.reference.call) @reference.call

(using_directive
  (identifier) @name.reference.import) @reference.import

(using_directive
  (qualified_name) @name.reference.import) @reference.import

(base_list
  (identifier) @name.reference.inherit) @reference.inherit

(base_list
  (generic_name
    (identifier) @name.reference.inherit)) @reference.inherit
